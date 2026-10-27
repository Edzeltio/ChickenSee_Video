#!/usr/bin/env python3
"""Collect reviewed YOLO training candidates and detector crops from video/cameras.

The generated labels are model predictions, not ground truth. Inspect and correct
the saved labels before using these files for training or evaluation.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import cv2
from ultralytics import YOLO

from analytics.config import CONFIG
from train import CLASS_NAMES


def _source_key(source: str) -> str:
    name = "camera" if source.isdigit() else Path(source).stem
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "source"
    suffix = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:48]}-{suffix}"


def _model_class_map(model_names) -> dict[str, int]:
    names = model_names if isinstance(model_names, dict) else dict(enumerate(model_names))
    names = {int(class_id): name for class_id, name in names.items()}
    expected = {name: class_id for class_id, name in CLASS_NAMES.items()}
    unknown = sorted(set(names.values()) - set(expected))
    if unknown:
        raise ValueError(
            "The selected model has class names not present in train.py: "
            + ", ".join(unknown)
        )
    return expected


def _reset_trackers(model) -> None:
    predictor = getattr(model, "predictor", None)
    for tracker in getattr(predictor, "trackers", []):
        tracker.reset()


def collect_source(
    model,
    source: str,
    output_root: Path,
    confidence: float = 0.25,
    image_size: int = 640,
    sample_interval_s: float = 2.0,
    max_duration_s: float = 0.0,
    max_frames: int = 0,
    device: Optional[str] = None,
    save_full_frames: bool = True,
) -> dict:
    class_ids = _model_class_map(model.names)
    capture_source = int(source) if source.isdigit() else source
    capture = cv2.VideoCapture(capture_source)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video/camera source: {source}")

    fps = capture.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 20.0
    sample_period_frames = max(1, round(fps * sample_interval_s))
    source_key = _source_key(source)
    image_dir = output_root / "images" / source_key
    label_dir = output_root / "labels" / source_key
    crop_root = output_root / "crops"
    manifest_dir = output_root / "manifests"
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    crop_root.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{source_key}.jsonl"

    frame_index = 0
    sampled_frames = 0
    saved_crops = 0
    started_at = time.monotonic()
    source_started_at = datetime.datetime.now().astimezone().isoformat()
    _reset_trackers(model)

    try:
        with manifest_path.open("a", encoding="utf-8") as manifest:
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                if max_frames and frame_index >= max_frames:
                    break
                if max_duration_s and time.monotonic() - started_at >= max_duration_s:
                    break

                options = {
                    "persist": True,
                    "tracker": CONFIG.tracker_config,
                    "conf": confidence,
                    "imgsz": image_size,
                    "verbose": False,
                }
                if device:
                    options["device"] = device
                result = model.track(frame, **options)[0]
                if frame_index % sample_period_frames == 0 and result.boxes is not None and len(result.boxes):
                    height, width = frame.shape[:2]
                    boxes = result.boxes.xyxy.cpu().numpy()
                    confidences = result.boxes.conf.cpu().numpy()
                    detected_classes = result.boxes.cls.int().cpu().tolist()
                    track_ids = (
                        result.boxes.id.int().cpu().tolist()
                        if result.boxes.id is not None
                        else [None] * len(boxes)
                    )
                    label_lines = []
                    image_name = f"frame_{frame_index:08d}.jpg"

                    for box_index, (box, score, class_id, track_id) in enumerate(
                        zip(boxes, confidences, detected_classes, track_ids)
                    ):
                        model_names = (
                            model.names
                            if isinstance(model.names, dict)
                            else dict(enumerate(model.names))
                        )
                        class_name = model_names.get(class_id, f"class_{class_id}")
                        if class_name not in class_ids:
                            raise ValueError(
                                f"Model class {class_name!r} is not in the project's "
                                "nine-class training map. Check your weights and labels."
                            )
                        class_number = class_ids[class_name]
                        x1, y1, x2, y2 = map(float, box)
                        x1 = max(0, min(width, x1))
                        x2 = max(0, min(width, x2))
                        y1 = max(0, min(height, y1))
                        y2 = max(0, min(height, y2))
                        box_width, box_height = x2 - x1, y2 - y1
                        if box_width < 2 or box_height < 2:
                            continue

                        label_lines.append(
                            f"{class_number} "
                            f"{((x1 + x2) / 2) / width:.6f} "
                            f"{((y1 + y2) / 2) / height:.6f} "
                            f"{box_width / width:.6f} {box_height / height:.6f}"
                        )
                        crop_name = (
                            f"{source_key}_frame_{frame_index:08d}_"
                            f"box_{box_index:03d}_conf_{float(score):.2f}.jpg"
                        )
                        crop_dir = crop_root / class_name / source_key
                        crop_dir.mkdir(parents=True, exist_ok=True)
                        crop = frame[int(y1):int(y2), int(x1):int(x2)]
                        if not cv2.imwrite(str(crop_dir / crop_name), crop):
                            raise OSError(f"Could not write object crop: {crop_dir / crop_name}")

                        record = {
                            "source": source,
                            "source_key": source_key,
                            "frame_index": frame_index,
                            "time_seconds": round(frame_index / fps, 3),
                            "track_id": track_id,
                            "class_id": class_number,
                            "class_name": class_name,
                            "confidence": round(float(score), 5),
                            "box_xyxy": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                            "crop": (Path("crops") / class_name / source_key / crop_name).as_posix(),
                            "auto_labeled": True,
                        }
                        manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
                        saved_crops += 1

                    if label_lines:
                        if save_full_frames:
                            image_path = image_dir / image_name
                            if not cv2.imwrite(str(image_path), frame):
                                raise OSError(f"Could not write dataset frame: {image_path}")
                            (label_dir / image_name).with_suffix(".txt").write_text(
                                "\n".join(label_lines) + "\n", encoding="utf-8"
                            )
                        sampled_frames += 1
                frame_index += 1
    finally:
        capture.release()

    return {
        "source": source,
        "source_key": source_key,
        "started_at": source_started_at,
        "frames_read": frame_index,
        "frames_saved": sampled_frames,
        "crops_saved": saved_crops,
        "manifest": str(manifest_path),
    }


def collect_manual_source(
    source: str,
    output_root: Path,
    sample_interval_s: float = 2.0,
    max_duration_s: float = 0.0,
    max_frames: int = 0,
) -> dict:
    """Interactively draw and label YOLO boxes without requiring model weights."""
    capture_source = int(source) if source.isdigit() else source
    capture = cv2.VideoCapture(capture_source)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video/camera source: {source}")

    fps = capture.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 20.0
    sample_period_frames = max(1, round(fps * sample_interval_s))
    source_key = _source_key(source)
    image_dir = output_root / "images" / source_key
    label_dir = output_root / "labels" / source_key
    crop_root = output_root / "crops"
    manifest_dir = output_root / "manifests"
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    crop_root.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{source_key}.jsonl"
    name_to_id = {name: class_id for class_id, name in CLASS_NAMES.items()}
    frame_index = sampled_frames = crops_saved = 0
    started_at = time.monotonic()
    source_started_at = datetime.datetime.now().astimezone().isoformat()
    window_name = "ChickenSee dataset labeling"

    try:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        with manifest_path.open("a", encoding="utf-8") as manifest:
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                if max_frames and frame_index >= max_frames:
                    break
                if max_duration_s and time.monotonic() - started_at >= max_duration_s:
                    break

                if frame_index % sample_period_frames:
                    frame_index += 1
                    continue

                height, width = frame.shape[:2]
                boxes = []
                preview = frame.copy()
                saved = False
                while True:
                    prompt = (
                        "R: select chicken/object | S: save frame | N: skip | Q: quit"
                    )
                    cv2.putText(
                        preview, prompt, (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 255), 2, cv2.LINE_AA,
                    )
                    cv2.imshow(window_name, preview)
                    key = cv2.waitKey(0) & 0xFF
                    if key in (ord("q"), ord("Q"), 27):
                        return {
                            "source": source,
                            "source_key": source_key,
                            "started_at": source_started_at,
                            "frames_read": frame_index + 1,
                            "frames_saved": sampled_frames,
                            "crops_saved": crops_saved,
                            "manifest": str(manifest_path),
                        }
                    if key in (ord("n"), ord("N")):
                        break
                    if key in (ord("s"), ord("S")):
                        image_name = f"frame_{frame_index:08d}.jpg"
                        image_path = image_dir / image_name
                        if not cv2.imwrite(str(image_path), frame):
                            raise OSError(f"Could not write dataset frame: {image_path}")
                        label_lines = [
                            f"{class_id} {((x1 + x2) / 2) / width:.6f} "
                            f"{((y1 + y2) / 2) / height:.6f} "
                            f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"
                            for class_id, x1, y1, x2, y2 in boxes
                        ]
                        (label_dir / image_name).with_suffix(".txt").write_text(
                            "\n".join(label_lines) + ("\n" if label_lines else ""),
                            encoding="utf-8",
                        )
                        for box_index, (class_id, x1, y1, x2, y2) in enumerate(boxes):
                            class_name = CLASS_NAMES[class_id]
                            crop_name = f"{source_key}_frame_{frame_index:08d}_box_{box_index:03d}.jpg"
                            crop_dir = crop_root / class_name / source_key
                            crop_dir.mkdir(parents=True, exist_ok=True)
                            crop = frame[y1:y2, x1:x2]
                            if not cv2.imwrite(str(crop_dir / crop_name), crop):
                                raise OSError(f"Could not write object crop: {crop_dir / crop_name}")
                            manifest.write(json.dumps({
                                "source": source,
                                "source_key": source_key,
                                "frame_index": frame_index,
                                "time_seconds": round(frame_index / fps, 3),
                                "class_id": class_id,
                                "class_name": class_name,
                                "box_xyxy": [x1, y1, x2, y2],
                                "crop": (Path("crops") / class_name / source_key / crop_name).as_posix(),
                                "auto_labeled": False,
                            }, ensure_ascii=False) + "\n")
                            crops_saved += 1
                        sampled_frames += 1
                        break
                    if key in (ord("r"), ord("R")):
                        x, y, box_width, box_height = cv2.selectROI(
                            window_name, frame, showCrosshair=True, fromCenter=False
                        )
                        if box_width <= 0 or box_height <= 0:
                            preview = frame.copy()
                            continue
                        class_value = input(
                            "Class name or class id (" + ", ".join(
                                f"{class_id}={name}" for class_id, name in CLASS_NAMES.items()
                            ) + "): "
                        ).strip()
                        try:
                            class_id = int(class_value) if class_value.isdigit() else name_to_id[class_value]
                            if class_id not in CLASS_NAMES:
                                raise KeyError(class_id)
                        except (KeyError, ValueError):
                            print(f"Unknown class {class_value!r}; box was skipped.")
                            preview = frame.copy()
                            continue
                        x1, y1 = max(0, int(x)), max(0, int(y))
                        x2, y2 = min(width, int(x + box_width)), min(height, int(y + box_height))
                        boxes.append((class_id, x1, y1, x2, y2))
                        cv2.rectangle(preview, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        cv2.putText(
                            preview, CLASS_NAMES[class_id], (x1, max(16, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                        )
                frame_index += 1
    finally:
        capture.release()
        cv2.destroyAllWindows()

    return {
        "source": source,
        "source_key": source_key,
        "started_at": source_started_at,
        "frames_read": frame_index,
        "frames_saved": sampled_frames,
        "crops_saved": crops_saved,
        "manifest": str(manifest_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Collect model-labeled object crops and YOLO frame proposals"
    )
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument(
        "--input",
        nargs="+",
        metavar="VIDEO",
        help="One or more local video paths",
    )
    sources.add_argument(
        "--camera",
        type=int,
        metavar="INDEX",
        help="Local camera index (for example 0)",
    )
    parser.add_argument("--weights", default=CONFIG.custom_weights_path)
    parser.add_argument("--output-dir", type=Path, default=Path("dataset/collection"))
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--image-size", type=int, default=640)
    parser.add_argument("--sample-interval", type=float, default=2.0, help="Seconds between saved frames")
    parser.add_argument("--duration", type=float, default=0.0, help="Maximum camera/video duration in seconds")
    parser.add_argument("--max-frames", type=int, default=0, help="Optional frame limit")
    parser.add_argument("--device", help="Ultralytics device selector, such as cpu or 0")
    parser.add_argument("--crops-only", action="store_true", help="Save object crops and manifest, not full frames/labels")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Draw boxes and enter class labels interactively; no model weights required",
    )
    args = parser.parse_args()

    if not 0 < args.confidence < 1:
        parser.error("--confidence must be greater than 0 and less than 1")
    if args.image_size < 32 or args.sample_interval <= 0:
        parser.error("--image-size must be at least 32 and --sample-interval must be positive")
    if args.duration < 0 or args.max_frames < 0:
        parser.error("--duration and --max-frames cannot be negative")
    if args.camera is not None and not args.duration and not args.max_frames:
        parser.error("A live camera needs --duration or --max-frames to bound the capture")

    if not args.manual and not Path(args.weights).is_file():
        parser.error(
            f"Model weights not found: {args.weights}. Set --weights to the trained "
            "custom_poultry.pt file or another compatible nine-class model."
        )

    if args.manual and args.crops_only:
        parser.error("--crops-only cannot be used with --manual; manual mode also creates YOLO labels")

    output_root = args.output_dir.expanduser().resolve()
    output_root = output_root / datetime.datetime.now().strftime("collection_%Y%m%d_%H%M%S")
    output_root.mkdir(parents=True, exist_ok=False)
    sources_to_collect = [str(args.camera)] if args.camera is not None else args.input

    model = None if args.manual else YOLO(args.weights)
    for source in sources_to_collect:
        if not source.isdigit() and not Path(source).is_file():
            parser.error(f"Input video does not exist or is not a file: {source}")
        if args.manual:
            result = collect_manual_source(
                source=source,
                output_root=output_root,
                sample_interval_s=args.sample_interval,
                max_duration_s=args.duration,
                max_frames=args.max_frames,
            )
        else:
            result = collect_source(
                model=model,
                source=source,
                output_root=output_root,
                confidence=args.confidence,
                image_size=args.image_size,
                sample_interval_s=args.sample_interval,
                max_duration_s=args.duration,
                max_frames=args.max_frames,
                device=args.device,
                save_full_frames=not args.crops_only,
            )
        print(json.dumps(result, ensure_ascii=False))
    print(f"Collection saved under: {output_root}")


if __name__ == "__main__":
    main()

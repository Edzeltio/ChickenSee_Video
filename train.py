#!/usr/bin/env python3
"""Validate, split, and train the ChickenSee YOLO detection dataset.

Importing this module is safe; training only starts from the command line.
"""

import argparse
import random
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from device_utils import resolve_torch_device
from model_registry import POULTRY_CLASS_NAMES, activate_model


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_DIR = PROJECT_DIR / "dataset"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
CLASS_NAMES = dict(enumerate(POULTRY_CLASS_NAMES))


def _collect_pairs(dataset_dir: Path):
    images_dir = dataset_dir / "images"
    labels_dir = dataset_dir / "labels"
    if not images_dir.is_dir() or not labels_dir.is_dir():
        raise FileNotFoundError(
            f"Expected paired source folders at {images_dir} and {labels_dir}. "
            "Collect and review images/labels before training."
        )

    images = sorted(
        path for path in images_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise ValueError(f"No images found under {images_dir}")

    pairs = []
    expected_labels = set()
    for image_path in images:
        relative = image_path.relative_to(images_dir)
        label_path = (labels_dir / relative).with_suffix(".txt")
        if not label_path.is_file():
            raise ValueError(f"Missing YOLO label file for image: {image_path}")
        _validate_label_file(label_path)
        pairs.append((relative, label_path.relative_to(labels_dir)))
        expected_labels.add(label_path.resolve())

    orphan_labels = [
        path for path in labels_dir.rglob("*.txt")
        if path.resolve() not in expected_labels
    ]
    if orphan_labels:
        raise ValueError(f"Found label files without matching images, e.g. {orphan_labels[0]}")
    return pairs


def _validate_label_file(path: Path) -> None:
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        values = line.split()
        if not values:
            continue
        if len(values) != 5:
            raise ValueError(f"{path}:{line_number}: expected class x_center y_center width height")
        try:
            class_id = int(values[0])
            x_center, y_center, width, height = map(float, values[1:])
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: labels must be numeric") from error
        if class_id not in CLASS_NAMES:
            raise ValueError(f"{path}:{line_number}: class id {class_id} is outside 0..{len(CLASS_NAMES) - 1}")
        if not all(0 <= value <= 1 for value in (x_center, y_center, width, height)):
            raise ValueError(f"{path}:{line_number}: normalized coordinates must be between 0 and 1")
        if width <= 0 or height <= 0:
            raise ValueError(f"{path}:{line_number}: box width and height must be greater than zero")


def _existing_split_counts(dataset_dir: Path):
    split_dirs = [
        dataset_dir / "train" / "images",
        dataset_dir / "train" / "labels",
        dataset_dir / "val" / "images",
        dataset_dir / "val" / "labels",
    ]
    populated = [path for path in split_dirs if path.exists() and any(path.iterdir())]
    if not populated:
        return None
    if len(populated) != 4:
        raise ValueError(
            "An existing train/val split is incomplete. Review it manually; "
            "the training script will not overwrite or repair split folders."
        )

    counts = []
    for split in ("train", "val"):
        images_dir = dataset_dir / split / "images"
        labels_dir = dataset_dir / split / "labels"
        images = [
            path for path in images_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
        if not images:
            raise ValueError(f"Existing {split} split has no images")
        for image_path in images:
            label_path = labels_dir / image_path.relative_to(images_dir).with_suffix(".txt")
            if not label_path.is_file():
                raise ValueError(f"Missing label for existing split image: {image_path}")
            _validate_label_file(label_path)
        counts.append(len(images))
    return tuple(counts)


def split_dataset(
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    val_ratio: float = 0.20,
    seed: int = 42,
    random_split: bool = False,
    group_regex: str | None = None,
):
    """Create train/val folders without ever replacing an existing split.

    By default, source subdirectories are kept together so frames from one
    recording cannot leak into both train and validation. Flat collections
    require an explicit --random-split or a --group-regex that extracts the
    source-video key from each relative image path.
    """
    if not 0 < val_ratio < 1:
        raise ValueError("Validation ratio must be greater than 0 and less than 1")

    existing = _existing_split_counts(dataset_dir)
    if existing is not None:
        return existing

    pairs = _collect_pairs(dataset_dir)
    if len(pairs) < 2:
        raise ValueError("At least two labeled images are required for a train/validation split")

    compiled_group_pattern = re.compile(group_regex) if group_regex else None
    groups = {}
    image_groups = {}
    for image_relative, label_relative in pairs:
        if random_split:
            group_key = str(image_relative)
        elif compiled_group_pattern:
            match = compiled_group_pattern.search(image_relative.as_posix())
            if not match:
                raise ValueError(f"Group pattern did not match image path: {image_relative}")
            group_key = match.group(1) if match.groups() else match.group(0)
        else:
            group_key = image_relative.parent.as_posix()
            if group_key == ".":
                raise ValueError(
                    "Images are in one flat folder, so source-video leakage cannot be prevented. "
                    "Put each recording in its own subfolder or pass --group-regex. "
                    "Use --random-split only if that leakage risk is acceptable."
                )
        groups.setdefault(group_key, []).append((image_relative, label_relative))
        image_groups[image_relative] = group_key

    if len(groups) < 2:
        raise ValueError("At least two source-video groups are required for a group-aware split")

    group_keys = sorted(groups)
    random.Random(seed).shuffle(group_keys)
    target_val_count = max(1, round(len(pairs) * val_ratio))
    val_keys = []
    val_count = 0
    for group_key in group_keys[:-1]:
        if val_count >= target_val_count:
            break
        val_keys.append(group_key)
        val_count += len(groups[group_key])
    if not val_keys:
        val_keys.append(group_keys[0])
        val_count = len(groups[group_keys[0]])
    val_key_set = set(val_keys)

    for split in ("train", "val"):
        (dataset_dir / split / "images").mkdir(parents=True, exist_ok=True)
        (dataset_dir / split / "labels").mkdir(parents=True, exist_ok=True)

    for image_relative, label_relative in pairs:
        split = "val" if image_groups[image_relative] in val_key_set else "train"
        for source_root, relative in (
            (dataset_dir / "images", image_relative),
            (dataset_dir / "labels", label_relative),
        ):
            destination = dataset_dir / split / source_root.name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / relative, destination)

    return len(pairs) - val_count, val_count


def write_dataset_yaml(dataset_dir: Path) -> Path:
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("PyYAML is required; install analytics/requirements.txt") from error

    config = {
        "path": dataset_dir.resolve().as_posix(),
        "train": "train/images",
        "val": "val/images",
        "nc": len(CLASS_NAMES),
        "names": [CLASS_NAMES[index] for index in range(len(CLASS_NAMES))],
    }
    config_path = PROJECT_DIR / "dataset_config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return config_path


def main():
    parser = argparse.ArgumentParser(description="Validate and train the ChickenSee YOLO dataset")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--base-model", default="yolo26n.pt", help="Ultralytics base weights or local .pt path")
    parser.add_argument("--epochs", type=int, default=150)
    parser.add_argument("--image-size", type=int, default=640)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--val-ratio", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--group-regex", help="Regex over relative image paths; capture group 1 is the recording key")
    parser.add_argument(
        "--random-split",
        action="store_true",
        help="Allow image-level random splitting; frames from the same video may leak across splits",
    )
    parser.add_argument("--name", default="poultry")
    parser.add_argument(
        "--device",
        default="auto",
        help="auto, cpu, mps, or CUDA index such as 0 (default: auto)",
    )
    args = parser.parse_args()

    if args.epochs < 1 or args.image_size < 32 or args.batch < 1:
        parser.error("epochs, image size, and batch must be positive")

    try:
        train_count, val_count = split_dataset(
            dataset_dir=args.dataset_dir,
            val_ratio=args.val_ratio,
            seed=args.seed,
            random_split=args.random_split,
            group_regex=args.group_regex,
        )
        config_path = write_dataset_yaml(args.dataset_dir)
        # Import only after input validation; importing train.py has no side effects.
        from ultralytics import YOLO

        model = YOLO(args.base_model)
        train_options = {
            "data": str(config_path),
            "epochs": args.epochs,
            "imgsz": args.image_size,
            "batch": args.batch,
            "close_mosaic": min(15, max(0, args.epochs - 1)),
            "name": args.name,
            "exist_ok": True,
            "project": str(PROJECT_DIR / "runs" / "detect"),
            "degrees": 10.0,
            "fliplr": 0.5,
            "mosaic": 1.0,
            "scale": 0.9,
            "hsv_h": 0.015,
            "hsv_s": 0.7,
            "hsv_v": 0.4,
            "patience": 30,
            "save_period": 25,
            "seed": args.seed,
            "verbose": True,
        }
        train_options["device"] = resolve_torch_device(args.device)

        print(f"Training images: {train_count}; validation images: {val_count}")
        print(f"Dataset config: {config_path}")
        model.train(**train_options)

        best_weights = PROJECT_DIR / "runs" / "detect" / args.name / "weights" / "best.pt"
        if not best_weights.is_file():
            raise FileNotFoundError(f"Training completed without the expected checkpoint: {best_weights}")
        model_dir = PROJECT_DIR / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", args.name).strip(".-_") or "poultry"
        version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target_weights = model_dir / f"{safe_name}-{version}.pt"
        shutil.copy2(best_weights, target_weights)
        target_weights.touch()
        manifest = activate_model(target_weights, PROJECT_DIR)
        print(f"Best weights saved as version: {target_weights}")
        print(f"Activated weights SHA-256: {manifest['sha256']}")
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()

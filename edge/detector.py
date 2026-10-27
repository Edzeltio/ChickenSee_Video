"""
edge/detector.py - Production-grade YOLO26n Inference & Poultry Re-ID Engine.
Optimized for continuous 12-hour operation on Dell OptiPlex edge hardware.
"""

import os
import cv2
import time
import torch
import numpy as np
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from ultralytics import YOLO

from .config import CONFIG, VisionConfig
from legband import (
    BAND_CLASS_TO_HEN,
    detect_leg_band_color,
    suppress_overlapping_equipment,
)


# ---------------------------------------------------------------------------
# Leg Band HSV Ranges & Color Specifications
# ---------------------------------------------------------------------------

COLOR_MAP = {
    "Rooster":       {"hex": "#FF0000", "rgb": (255, 0, 0),     "bgr": (0, 0, 255)},
    "Chicken":       {"hex": "#FFA500", "rgb": (255, 165, 0),   "bgr": (0, 165, 255)},
    "Hen1 (Green)":  {"hex": "#00FF00", "rgb": (0, 255, 0),     "bgr": (0, 255, 0)},
    "Hen2 (White)":  {"hex": "#FFFFFF", "rgb": (255, 255, 255), "bgr": (255, 255, 255)},
    "Hen3 (Blue)":   {"hex": "#0000FF", "rgb": (0, 0, 255),     "bgr": (255, 0, 0)},
    "Hen4 (Yellow)": {"hex": "#FFFF00", "rgb": (255, 255, 0),   "bgr": (0, 255, 255)},
    "Hen5 (Pink)":   {"hex": "#FF69B4", "rgb": (255, 105, 180), "bgr": (180, 105, 255)},
    "Feeder":        {"hex": "#808080", "rgb": (128, 128, 128), "bgr": (128, 128, 128)},
    "Drinker":       {"hex": "#808080", "rgb": (128, 128, 128), "bgr": (128, 128, 128)},
    "Default":       {"hex": "#808080", "rgb": (128, 128, 128), "bgr": (128, 128, 128)},
}

@dataclass
class Detection:
    box: List[int]                # [x1, y1, x2, y2]
    track_id: Optional[int]       # ByteTrack persistent ID
    label: str                    # e.g., "Rooster", "Hen1 (Green)", "Feeder"
    confidence: float             # Model detection confidence
    type: str                     # "rooster" | "hen" | "infrastructure"
    centroid: Tuple[int, int]     # (cx, cy)
    color_hex: str
    color_bgr: Tuple[int, int, int]
    raw_cls_id: int = 0


@dataclass
class FrameInferenceResult:
    frame_idx: int
    timestamp: float
    detections: List[Detection] = field(default_factory=list)
    birds: List[Detection] = field(default_factory=list)
    infrastructure: List[Detection] = field(default_factory=list)
    total_birds: int = 0
    roosters: int = 0
    hens_identified: int = 0
    unidentified_chickens: int = 0
    fps: float = 0.0
    inference_time_ms: float = 0.0


class PoultryInferenceEngine:
    """
    High-performance Edge YOLO26n inference and ByteTrack manager.
    Designed for resource-constrained Dell OptiPlex edge deployment.
    """

    def __init__(self, vision_config: Optional[VisionConfig] = None):
        self.cfg = vision_config or CONFIG.vision
        
        # 1. Hardware Detection & Threading Optimization for OptiPlex
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cpu":
            num_threads = min(self.cfg.num_cpu_threads, os.cpu_count() or 4)
            torch.set_num_threads(num_threads)
            print(f"[ENGINE] Hardware: Dell OptiPlex CPU mode ({num_threads} threads configured)")
        else:
            gpu_name = torch.cuda.get_device_name(0)
            print(f"[ENGINE] Hardware: CUDA GPU accelerated ({gpu_name})")

        # 2. Model Loading
        model_path = self.cfg.model_path
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"[ENGINE] Model weights not found at: {model_path}")

        print(f"[ENGINE] Loading custom poultry weights: {model_path}")
        self.model = YOLO(model_path)
        self.class_names = self.model.names if hasattr(self.model, "names") else {}
        print(f"[ENGINE] Classes loaded ({len(self.class_names)}): {self.class_names}")

        # 3. Tracker Configuration
        tracker_path = self.cfg.tracker_config
        if not os.path.isfile(tracker_path):
            print(f"[ENGINE] Tracker config '{tracker_path}' not found on disk, using built-in 'bytetrack.yaml'")
            self.tracker_file = "bytetrack.yaml"
        else:
            self.tracker_file = tracker_path
            print(f"[ENGINE] Using custom poultry tracker config: {self.tracker_file}")

        # 4. State Management & Memory Safe History
        self.frame_count = 0
        self.track_states: Dict[int, Dict[str, Any]] = {}   # track_id -> {"label": str, "last_seen": int}
        self.smoothed_boxes: Dict[int, List[float]] = {}    # track_id -> [x1, y1, x2, y2]
        self.cumulative_seen_ids = set()
        self.short_id_map: Dict[int, int] = {}
        self._next_short_id = 1
        
        # Edge memory hygiene: prune inactive tracks after N frames
        self.max_inactive_frames = 300  # ~20 seconds at 15 FPS

        # 5. Warm-up inference
        self._warmup()

    def _warmup(self):
        """Warm up model graph to prevent first-frame latency spike."""
        try:
            dummy = np.zeros((self.cfg.inference_size, self.cfg.inference_size, 3), dtype=np.uint8)
            with torch.inference_mode():
                self.model(dummy, verbose=False, imgsz=self.cfg.inference_size)
            print("[ENGINE] Inference engine warmed up successfully.")
        except Exception as e:
            print(f"[ENGINE] Warm-up notice: {e}")

    def _resolve_short_id(self, raw_tid: Optional[int]) -> Optional[int]:
        if raw_tid is None:
            return None
        if raw_tid not in self.short_id_map:
            self.short_id_map[raw_tid] = self._next_short_id
            self._next_short_id += 1
        return self.short_id_map[raw_tid]

    def _smooth_box(self, track_id: Optional[int], raw_box: List[float]) -> List[float]:
        if track_id is None:
            return raw_box
        if track_id not in self.smoothed_boxes:
            self.smoothed_boxes[track_id] = list(raw_box)
            return raw_box
        prev = self.smoothed_boxes[track_id]
        smoothed = [0.4 * r + 0.6 * p for r, p in zip(raw_box, prev)]
        self.smoothed_boxes[track_id] = smoothed
        return smoothed

    def _prune_expired_tracks(self):
        """Memory-safe LRU eviction: prevents dictionary bloating over 12-hour continuous runs."""
        if self.frame_count % 300 != 0:
            return
        
        cutoff = self.frame_count - self.max_inactive_frames
        expired = [
            tid for tid, s in self.track_states.items() 
            if s.get("last_seen_frame", 0) < cutoff
        ]
        for tid in expired:
            self.track_states.pop(tid, None)
            self.smoothed_boxes.pop(tid, None)

    def process_frame(self, frame: np.ndarray, timestamp: Optional[float] = None) -> FrameInferenceResult:
        """
        Process a single video frame: detection, tracking, poultry Re-ID, and output formatting.
        """
        self.frame_count += 1
        if timestamp is None:
            timestamp = time.time()

        t_start = time.perf_counter()

        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return FrameInferenceResult(frame_idx=self.frame_count, timestamp=timestamp)

        orig_h, orig_w = frame.shape[:2]

        # Input downscaling for OptiPlex speed if needed
        work_frame = frame
        scale_w = 1.0
        scale_h = 1.0
        if self.cfg.target_width and orig_w > self.cfg.target_width:
            target_h = int(orig_h * (self.cfg.target_width / orig_w))
            work_frame = cv2.resize(frame, (self.cfg.target_width, target_h), interpolation=cv2.INTER_AREA)
            scale_w = orig_w / self.cfg.target_width
            scale_h = orig_h / target_h

        # ------------------------------------------------------------------
        # 1. ByteTrack YOLO Inference
        # ------------------------------------------------------------------
        try:
            with torch.inference_mode():
                results = self.model.track(
                    work_frame,
                    persist=True,
                    tracker=self.tracker_file,
                    verbose=False,
                    conf=self.cfg.conf_threshold,
                    iou=self.cfg.iou_threshold,
                    agnostic_nms=False,
                    imgsz=self.cfg.inference_size,
                    device=self.device
                )[0]
        except Exception as e:
            # Fallback to direct detection if tracker fails
            try:
                with torch.inference_mode():
                    results = self.model(
                        work_frame,
                        verbose=False,
                        conf=self.cfg.conf_threshold,
                        iou=self.cfg.iou_threshold,
                        agnostic_nms=False,
                        imgsz=self.cfg.inference_size,
                        device=self.device
                    )[0]
            except Exception:
                return FrameInferenceResult(frame_idx=self.frame_count, timestamp=timestamp)

        infer_dur = (time.perf_counter() - t_start) * 1000.0
        fps = 1000.0 / infer_dur if infer_dur > 0 else 0.0

        n_boxes = len(results.boxes) if results.boxes is not None else 0
        if n_boxes == 0:
            return FrameInferenceResult(
                frame_idx=self.frame_count,
                timestamp=timestamp,
                inference_time_ms=infer_dur,
                fps=fps
            )

        boxes = results.boxes.xyxy.cpu().numpy()
        confs = results.boxes.conf.cpu().numpy()
        cls_ids = results.boxes.cls.int().cpu().tolist()
        track_ids = (
            results.boxes.id.int().cpu().tolist()
            if results.boxes.id is not None
            else [None] * n_boxes
        )

        chicken_raw = []
        rooster_raw = []
        band_raw    = []
        infra_raw   = []

        for box, conf_val, cls_id, track_id in zip(boxes, confs, cls_ids, track_ids):
            name = self.class_names.get(cls_id, f"class_{cls_id}")
            entry = (box, float(conf_val), name, track_id, cls_id)

            if name == "Chicken":
                chicken_raw.append(entry)
            elif name == "Rooster":
                rooster_raw.append(entry)
            elif name.startswith("Leg_Band_"):
                band_raw.append((box, name))
            elif name in ("Feeder", "Drinker"):
                infra_raw.append(entry)
        infra_raw = suppress_overlapping_equipment(infra_raw)

        # ------------------------------------------------------------------
        # 2. Leg Band Association & Re-ID for Hens
        # ------------------------------------------------------------------
        for (band_box, band_class) in band_raw:
            bx1, by1, bx2, by2 = band_box
            band_cx = (bx1 + bx2) / 2
            band_cy = (by1 + by2) / 2

            for (cbox, _, _, track_id, _) in chicken_raw:
                if track_id is None:
                    continue
                cx1, cy1, cx2, cy2 = cbox
                # Proximity expansion to catch leg band near lower chicken body
                if (cx1 - 10 <= band_cx <= cx2 + 10) and (cy1 - 10 <= band_cy <= cy2 + 10):
                    color_name = BAND_CLASS_TO_HEN.get(band_class)
                    if color_name:
                        self.track_states[track_id] = {
                            "label": color_name,
                            "last_seen_frame": self.frame_count
                        }
                    break

        # ------------------------------------------------------------------
        # 3. HSV fallback on the original-resolution feet crop
        # ------------------------------------------------------------------
        for (cbox, _, _, track_id, _) in chicken_raw:
            if track_id is None or (track_id in self.track_states and self.track_states[track_id]["label"] != "Chicken"):
                continue

            source_box = (
                cbox[0] * scale_w,
                cbox[1] * scale_h,
                cbox[2] * scale_w,
                cbox[3] * scale_h,
            )
            best_name = detect_leg_band_color(frame, source_box)
            if best_name:
                self.track_states[track_id] = {
                    "label": best_name,
                    "last_seen_frame": self.frame_count
                }

        # ------------------------------------------------------------------
        # 4. Construct Output Detections
        # ------------------------------------------------------------------
        detections: List[Detection] = []
        birds: List[Detection] = []
        infra: List[Detection] = []

        # Process Hens / Chickens
        hens_id_count = 0
        unid_count = 0
        assigned_hen_labels = set()

        for (cbox, conf_val, _, track_id, cls_id) in chicken_raw:
            raw_orig = [cbox[0] * scale_w, cbox[1] * scale_h, cbox[2] * scale_w, cbox[3] * scale_h]
            x1, y1, x2, y2 = self._smooth_box(track_id, raw_orig)
            sid = self._resolve_short_id(track_id)

            if track_id is not None:
                self.cumulative_seen_ids.add(track_id)

            label = "Chicken"
            if track_id is not None and track_id in self.track_states:
                cand = self.track_states[track_id]["label"]
                if cand not in assigned_hen_labels:
                    label = cand
                    assigned_hen_labels.add(cand)
                self.track_states[track_id]["last_seen_frame"] = self.frame_count

            if label.startswith("Hen"):
                hens_id_count += 1
            else:
                unid_count += 1

            cinfo = COLOR_MAP.get(label, COLOR_MAP["Chicken"])
            cx = int((x1 + x2) / 2)
            cy = int((y1 + y2) / 2)

            det = Detection(
                box=[int(x1), int(y1), int(x2), int(y2)],
                track_id=sid,
                label=label,
                confidence=round(conf_val, 3),
                type="hen",
                centroid=(cx, cy),
                color_hex=cinfo["hex"],
                color_bgr=cinfo["bgr"],
                raw_cls_id=cls_id
            )
            detections.append(det)
            birds.append(det)

        # Process Roosters (Always distinct, red bounding box)
        rooster_count = 0
        for (rbox, conf_val, _, track_id, cls_id) in rooster_raw:
            raw_orig = [rbox[0] * scale_w, rbox[1] * scale_h, rbox[2] * scale_w, rbox[3] * scale_h]
            x1, y1, x2, y2 = self._smooth_box(track_id, raw_orig)
            sid = self._resolve_short_id(track_id)

            if track_id is not None:
                self.cumulative_seen_ids.add(track_id)

            rooster_count += 1
            cinfo = COLOR_MAP["Rooster"]
            cx = int((x1 + x2) / 2)
            cy = int((y1 + y2) / 2)

            det = Detection(
                box=[int(x1), int(y1), int(x2), int(y2)],
                track_id=sid,
                label="Rooster",
                confidence=round(conf_val, 3),
                type="rooster",
                centroid=(cx, cy),
                color_hex=cinfo["hex"],
                color_bgr=cinfo["bgr"],
                raw_cls_id=cls_id
            )
            detections.append(det)
            birds.append(det)

        # Process Infrastructure (Feeder & Drinker)
        for (ibox, conf_val, iname, _, cls_id) in infra_raw:
            x1 = int(ibox[0] * scale_w)
            y1 = int(ibox[1] * scale_h)
            x2 = int(ibox[2] * scale_w)
            y2 = int(ibox[3] * scale_h)
            cinfo = COLOR_MAP.get(iname, COLOR_MAP["Default"])
            cx = int((x1 + x2) / 2)
            cy = int((y1 + y2) / 2)

            det = Detection(
                box=[x1, y1, x2, y2],
                track_id=None,
                label=iname,
                confidence=round(conf_val, 3),
                type="infrastructure",
                centroid=(cx, cy),
                color_hex=cinfo["hex"],
                color_bgr=cinfo["bgr"],
                raw_cls_id=cls_id
            )
            detections.append(det)
            infra.append(det)

        # 5. Clean up expired tracks
        self._prune_expired_tracks()

        return FrameInferenceResult(
            frame_idx=self.frame_count,
            timestamp=timestamp,
            detections=detections,
            birds=birds,
            infrastructure=infra,
            total_birds=len(birds),
            roosters=rooster_count,
            hens_identified=hens_id_count,
            unidentified_chickens=unid_count,
            fps=round(fps, 1),
            inference_time_ms=round(infer_dur, 2)
        )

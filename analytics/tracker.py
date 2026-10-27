"""
analytics/tracker.py
Ultralytics YOLO26 Nano + ByteTrack Multi-Object Tracking Engine for Dell OptiPlex.
Executes model.track(..., tracker="bytetrack.yaml", persist=True) for temporal bird tracking.
"""

import math
import os
import cv2
import numpy as np
from collections import Counter, deque
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from ultralytics import YOLO

from .config import BASE_DIR, CONFIG, PROJECT_ROOT
from legband import (
    BAND_CLASS_TO_HEN,
    detect_leg_band_color,
    suppress_overlapping_equipment,
)


LABEL_COLORS_BGR = {
    "Rooster": (0, 0, 255),
    "Chicken": (0, 165, 255),
    "Hen1 (Green)": (0, 255, 0),
    "Hen2 (White)": (255, 255, 255),
    "Hen3 (Blue)": (255, 0, 0),
    "Hen4 (Yellow)": (0, 255, 255),
    "Hen5 (Pink)": (180, 105, 255),
    "Feeder": (128, 128, 128),
    "Drinker": (128, 128, 128),
    "Default": (128, 128, 128),
}


@dataclass
class TrackedDetection:
    box: List[int]                     # [x1, y1, x2, y2]
    centroid: Tuple[int, int]          # (cx, cy)
    confidence: float
    class_id: int
    class_name: str
    track_id: Optional[int]            # ByteTrack persistent ID
    bird_label: str                    # Hen1-5, Rooster, or Chicken


@dataclass
class TrackerFrameOutput:
    frame_idx: int
    timestamp_s: float
    birds: List[TrackedDetection]
    feeders: List[TrackedDetection]
    drinkers: List[TrackedDetection]


class ZampenYOLOTracker:
    """
    YOLO26 Nano + ByteTrack edge inference wrapper.
    Optimized for Dell OptiPlex multi-core CPU / Intel OpenVINO / CUDA execution.
    """

    # Requires 10 more observations than current class before switching —
    # prevents a handful of mislabelled frames from flipping Rooster→Hen.
    CLASS_SWITCH_MARGIN = 10
    # Keep per-track class history alive for 600 frames (~20 s at 30 fps).
    CLASS_HISTORY_TTL_FRAMES = 600
    MIN_DETECTION_CONFIDENCE = 0.70
    REIDENTIFICATION_MAX_GAP_FRAMES = 30
    REIDENTIFICATION_DISTANCE_RATIO = 0.08
    REIDENTIFICATION_MIN_DISTANCE_PX = 50.0

    def __init__(self, model_weights: Optional[str] = None, tracker_yaml: Optional[str] = None):
        self.weights_path = self._resolve_weights(model_weights or CONFIG.model_name)
        self.tracker_yaml = tracker_yaml or CONFIG.tracker_config
        self.device = CONFIG.device
        self.conf = CONFIG.conf_threshold
        self.iou = CONFIG.iou_threshold
        self.imgsz = CONFIG.imgsz

        print(f"[TRACKER] Initializing YOLO26 Nano from: {self.weights_path}")
        print(f"[TRACKER] Using ByteTrack config: {self.tracker_yaml}")
        self.model = YOLO(self.weights_path)

        # Standard poultry class mapping
        self.class_map = {
            0: "Chicken",
            1: "Drinker",
            2: "Feeder",
            3: "Leg_Band_Green",
            4: "Leg_Band_Pink",
            5: "Leg_Band_Violet",
            6: "Leg_Band_White",
            7: "Leg_Band_Yellow",
            8: "Rooster",
        }

        # Persistent ID Re-ID cache: track_id -> confirmed label
        self.track_label_cache: Dict[int, str] = {}
        self.track_label_votes: Dict[int, deque] = {}
        self.track_label_last_seen: Dict[int, int] = {}
        self.track_class_history: Dict[int, Dict[int, Tuple[int, float]]] = {}
        self.track_resolved_classes: Dict[int, int] = {}
        self.track_class_last_seen: Dict[int, int] = {}
        self.processed_frame_count = 0
        self.short_id_map: Dict[int, int] = {}
        self._next_short_id = 1
        self.short_track_positions: Dict[int, Tuple[Tuple[float, float], int]] = {}

    def _resolve_short_id(self, raw_track_id: Optional[int]) -> Optional[int]:
        if raw_track_id is None:
            return None
        if raw_track_id not in self.short_id_map:
            self.short_id_map[raw_track_id] = self._next_short_id
            self._next_short_id += 1
        return self.short_id_map[raw_track_id]

    def reset_video_state(self) -> None:
        self.track_label_cache.clear()
        self.track_label_votes.clear()
        self.track_label_last_seen.clear()
        self.track_class_history.clear()
        self.track_resolved_classes.clear()
        self.track_class_last_seen.clear()
        self.processed_frame_count = 0
        self.short_id_map.clear()
        self._next_short_id = 1
        self.short_track_positions.clear()

        predictor = getattr(self.model, "predictor", None)
        for tracker in getattr(predictor, "trackers", []):
            tracker.reset()

    def _assign_display_ids(
        self,
        birds: List[TrackedDetection],
        frame_shape: Tuple[int, ...],
    ) -> None:
        frame_diagonal = math.hypot(frame_shape[0], frame_shape[1])
        max_distance = max(
            self.REIDENTIFICATION_MIN_DISTANCE_PX,
            frame_diagonal * self.REIDENTIFICATION_DISTANCE_RATIO,
        )
        current_frame = self.processed_frame_count
        pending = []
        assigned_ids = set()

        for bird in birds:
            raw_track_id = bird.track_id
            if raw_track_id is None:
                continue

            display_id = self.short_id_map.get(raw_track_id)
            if display_id is not None:
                position = self.short_track_positions.get(display_id)
                if (
                    position is not None
                    and current_frame - position[1] > self.REIDENTIFICATION_MAX_GAP_FRAMES
                ):
                    self.short_id_map.pop(raw_track_id, None)
                    display_id = None

            if display_id is None:
                pending.append((bird, raw_track_id))
            else:
                bird.track_id = display_id
                assigned_ids.add(display_id)

        candidate_pairs = []
        for bird, raw_track_id in pending:
            x1, y1, x2, y2 = bird.box
            center = ((x1 + x2) / 2, (y1 + y2) / 2)
            for display_id, (last_center, last_seen) in self.short_track_positions.items():
                gap = current_frame - last_seen
                if display_id in assigned_ids or not 1 <= gap <= self.REIDENTIFICATION_MAX_GAP_FRAMES:
                    continue
                distance = math.dist(center, last_center)
                if distance <= max_distance:
                    candidate_pairs.append(
                        (distance, display_id, raw_track_id, bird, center)
                    )

        matched_raw_ids = set()
        for _, display_id, raw_track_id, bird, _ in sorted(
            candidate_pairs, key=lambda pair: pair[:3]
        ):
            if display_id in assigned_ids or raw_track_id in matched_raw_ids:
                continue
            for previous_raw_id, previous_display_id in list(self.short_id_map.items()):
                if previous_display_id == display_id:
                    self.short_id_map.pop(previous_raw_id)
            self.short_id_map[raw_track_id] = display_id
            bird.track_id = display_id
            assigned_ids.add(display_id)
            matched_raw_ids.add(raw_track_id)

        for bird, raw_track_id in pending:
            if raw_track_id in matched_raw_ids:
                continue
            display_id = self.short_id_map.get(raw_track_id)
            if display_id is None:
                display_id = self._next_short_id
                self._next_short_id += 1
                self.short_id_map[raw_track_id] = display_id
            bird.track_id = display_id
            assigned_ids.add(display_id)

        for bird in birds:
            if bird.track_id is None:
                continue
            x1, y1, x2, y2 = bird.box
            self.short_track_positions[bird.track_id] = (
                ((x1 + x2) / 2, (y1 + y2) / 2),
                current_frame,
            )

    def _record_track_label(self, track_id: int, label: str) -> None:
        last_seen = self.track_label_last_seen.get(track_id)
        # 15-frame rolling window; clear only after 90 consecutive unseen frames
        # so a bird that briefly ducks behind the feeder doesn't lose its identity.
        votes = self.track_label_votes.setdefault(track_id, deque(maxlen=15))
        if last_seen is not None and self.processed_frame_count - last_seen > 90:
            votes.clear()
        votes.append(label)
        self.track_label_last_seen[track_id] = self.processed_frame_count
        # Require 8 consistent observations before committing a label so random
        # per-frame HSV noise cannot flip a hen's colour identity.
        vote_counts = Counter(votes)
        resolved_label, count = vote_counts.most_common(1)[0]
        if count >= 8:
            # Once a label is locked, only replace it if the new winner has ≥8
            # votes AND beats the current cache by a large absolute margin.
            current = self.track_label_cache.get(track_id)
            if current is None or current == resolved_label:
                self.track_label_cache[track_id] = resolved_label
            else:
                current_count = vote_counts.get(current, 0)
                if count >= current_count + 5:
                    self.track_label_cache[track_id] = resolved_label

    def _resolve_weights(self, desired_name: str) -> str:
        """Finds or resolves the weights path, checking local files and fallbacks."""
        # 1. Exact path or in current/root directory
        candidates = [
            Path(desired_name),
            PROJECT_ROOT / desired_name,
            BASE_DIR / desired_name,
            Path(CONFIG.custom_weights_path),
            PROJECT_ROOT / "custom_poultry.pt",
            PROJECT_ROOT / "yolo26n.pt",
            PROJECT_ROOT / "yolo11n.pt",
            PROJECT_ROOT / "yolov8n.pt",
        ]

        for cand in candidates:
            if cand and cand.is_file():
                return str(cand.resolve())

        # If desired is a standard ultralytics weight name, YOLO will auto-download
        return desired_name

    def _resolve_track_class(
        self, cls_id: int, confidence: float, track_id: Optional[int]
    ) -> Tuple[int, str, float]:
        class_name = self.class_map.get(
            cls_id, self.model.names.get(cls_id, f"class_{cls_id}")
        )
        if track_id is None:
            return cls_id, class_name, confidence

        if class_name not in ("Chicken", "Rooster") and not class_name.startswith("Hen"):
            self.track_class_history.pop(track_id, None)
            self.track_resolved_classes.pop(track_id, None)
            self.track_class_last_seen.pop(track_id, None)
            return cls_id, class_name, confidence

        class_history = self.track_class_history.setdefault(track_id, {})
        observations, confidence_sum = class_history.get(cls_id, (0, 0.0))
        class_history[cls_id] = (observations + 1, confidence_sum + confidence)
        self.track_class_last_seen[track_id] = self.processed_frame_count

        candidate_cls_id = max(
            class_history,
            key=lambda candidate_id: (
                class_history[candidate_id][0],
                class_history[candidate_id][1] / class_history[candidate_id][0],
            ),
        )
        resolved_cls_id = self.track_resolved_classes.get(track_id, candidate_cls_id)
        if candidate_cls_id != resolved_cls_id:
            resolved_count = class_history.get(resolved_cls_id, (0, 0.0))[0]
            candidate_count = class_history[candidate_cls_id][0]
            # Extra protection: a track confirmed as Rooster (class 8) can only
            # switch away if the challenger has CLASS_SWITCH_MARGIN * 2 more
            # observations, guarding against occasional Rooster→Chicken flips.
            rooster_cls_id = next(
                (k for k, v in self.class_map.items() if v == "Rooster"), 8
            )
            margin = (
                self.CLASS_SWITCH_MARGIN * 2
                if resolved_cls_id == rooster_cls_id
                else self.CLASS_SWITCH_MARGIN
            )
            if candidate_count >= resolved_count + margin:
                resolved_cls_id = candidate_cls_id
        self.track_resolved_classes[track_id] = resolved_cls_id

        resolved_observations, resolved_confidence_sum = class_history[resolved_cls_id]
        resolved_name = self.class_map.get(
            resolved_cls_id,
            self.model.names.get(resolved_cls_id, f"class_{resolved_cls_id}"),
        )
        return (
            resolved_cls_id,
            resolved_name,
            resolved_confidence_sum / resolved_observations,
        )

    def _prune_track_class_history(self) -> None:
        if self.processed_frame_count % self.CLASS_HISTORY_TTL_FRAMES:
            return

        cutoff = self.processed_frame_count - self.CLASS_HISTORY_TTL_FRAMES
        expired = [
            track_id
            for track_id, last_seen in self.track_class_last_seen.items()
            if last_seen < cutoff
        ]
        for track_id in expired:
            self.track_class_history.pop(track_id, None)
            self.track_resolved_classes.pop(track_id, None)
            self.track_class_last_seen.pop(track_id, None)
            self.track_label_cache.pop(track_id, None)
            self.track_label_votes.pop(track_id, None)
            self.track_label_last_seen.pop(track_id, None)

    def process_frame(self, frame: np.ndarray, frame_idx: int, fps: float = 30.0) -> TrackerFrameOutput:
        """
        Executes YOLO26 Nano inference with ByteTrack persistence.
        """
        self.processed_frame_count += 1
        self._prune_track_class_history()
        timestamp_s = frame_idx / fps if fps > 0 else 0.0

        # Run ByteTrack prediction workflow
        results = self.model.track(
            frame,
            persist=True,
            tracker=self.tracker_yaml,
            conf=max(self.conf, self.MIN_DETECTION_CONFIDENCE),
            iou=self.iou,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False
        )[0]

        birds: List[TrackedDetection] = []
        feeders: List[TrackedDetection] = []
        drinkers: List[TrackedDetection] = []
        band_dets: List[Tuple[List[int], str]] = []

        if results.boxes is None or len(results.boxes) == 0:
            return TrackerFrameOutput(
                frame_idx=frame_idx,
                timestamp_s=timestamp_s,
                birds=[],
                feeders=[],
                drinkers=[]
            )

        boxes = results.boxes.xyxy.cpu().numpy()
        confs = results.boxes.conf.cpu().numpy()
        classes = results.boxes.cls.int().cpu().tolist()
        track_ids = (
            results.boxes.id.int().cpu().tolist()
            if results.boxes.id is not None
            else [None] * len(boxes)
        )

        # First pass: parse raw detections
        for box, conf, cls_id, track_id in zip(boxes, confs, classes, track_ids):
            cls_id, class_name, conf = self._resolve_track_class(
                cls_id, float(conf), track_id
            )
            x1, y1, x2, y2 = [int(v) for v in box]
            cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)

            det = TrackedDetection(
                box=[x1, y1, x2, y2],
                centroid=(cx, cy),
                confidence=float(conf),
                class_id=cls_id,
                class_name=class_name,
                track_id=track_id,
                bird_label=class_name,
            )

            if class_name in ("Chicken", "Rooster") or class_name.startswith("Hen"):
                det.bird_label = class_name
                birds.append(det)
            elif class_name == "Feeder":
                det.bird_label = "Feeder"
                det.track_id = None
                feeders.append(det)
            elif class_name == "Drinker":
                det.bird_label = "Drinker"
                det.track_id = None
                drinkers.append(det)
            elif class_name.startswith("Leg_Band_"):
                band_dets.append(([x1, y1, x2, y2], class_name))

        # Re-ID lock for leg bands if present.
        # Band detection is authoritative — record it even on a single frame
        # so it counts toward the 8-vote threshold without waiting.
        for band_box, band_name in band_dets:
            bx1, by1, bx2, by2 = band_box
            bcx, bcy = (bx1 + bx2) // 2, (by1 + by2) // 2
            for bird in birds:
                if bird.track_id is None or bird.class_name == "Rooster":
                    continue
                x1, y1, x2, y2 = bird.box
                if (x1 - 15 <= bcx <= x2 + 15) and (y1 - 15 <= bcy <= y2 + 15):
                    hen_label = BAND_CLASS_TO_HEN.get(band_name)
                    if hen_label:
                        # Cast 3 votes at once for an explicit YOLO band detection
                        # (it's far more reliable than the HSV crop fallback).
                        for _ in range(3):
                            self._record_track_label(bird.track_id, hen_label)
                    break

        for bird in birds:
            if bird.track_id is None or bird.class_name == "Rooster":
                continue
            hen_label = detect_leg_band_color(frame, bird.box)
            if hen_label:
                self._record_track_label(bird.track_id, hen_label)

        # Resolve active bird labels, enforcing 1-to-1 uniqueness in the current frame
        used_labels = set()
        def _label_priority(b: TrackedDetection) -> int:
            if b.track_id is None:
                return 0
            lbl = self.track_label_cache.get(b.track_id)
            if not lbl:
                return 0
            votes = self.track_label_votes.get(b.track_id, [])
            return votes.count(lbl)

        sorted_birds = sorted(birds, key=_label_priority, reverse=True)
        for bird in sorted_birds:
            if bird.track_id is not None and bird.track_id in self.track_label_cache:
                assigned = self.track_label_cache[bird.track_id]
                if assigned not in used_labels:
                    bird.bird_label = assigned
                    used_labels.add(assigned)
                else:
                    # Duplicate hen identity in the same frame is invalid; retain generic Chicken
                    bird.bird_label = "Chicken"
            elif bird.class_name == "Chicken":
                bird.bird_label = "Chicken"

        equipment = suppress_overlapping_equipment(
            [
                (detection.box, detection.confidence, detection.class_name, detection)
                for detection in feeders + drinkers
            ]
        )
        feeders = [
            detection for _, _, label, detection in equipment if label == "Feeder"
        ]
        drinkers = [
            detection for _, _, label, detection in equipment if label == "Drinker"
        ]

        self._assign_display_ids(birds, frame.shape)

        return TrackerFrameOutput(
            frame_idx=frame_idx,
            timestamp_s=timestamp_s,
            birds=birds,
            feeders=feeders,
            drinkers=drinkers
        )

    def annotate_frame(self, frame: np.ndarray, frame_out: TrackerFrameOutput) -> np.ndarray:
        """Draws bounding boxes, track IDs, and a detection HUD onto a copy of the frame."""
        vis = frame.copy()
        w = vis.shape[1]

        def _draw(det: TrackedDetection, fallback_name: str):
            x1, y1, x2, y2 = det.box
            if fallback_name in ("Feeder", "Drinker"):
                label = fallback_name
            else:
                label = det.bird_label if det.bird_label else det.class_name or fallback_name
            color = LABEL_COLORS_BGR.get(label, LABEL_COLORS_BGR.get(det.class_name, LABEL_COLORS_BGR["Default"]))
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
            tid = f"#{det.track_id} " if det.track_id is not None else ""
            text = f"{tid}{label} ({det.confidence:.2f})"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            y_text = max(th + 4, y1)
            cv2.rectangle(vis, (x1, y_text - th - 6), (x1 + tw + 6, y_text + 2), color, -1)
            ink = (0, 0, 0) if sum(color) > 400 else (255, 255, 255)
            cv2.putText(vis, text, (x1 + 3, y_text - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, ink, 1)

        for bird in frame_out.birds:
            _draw(bird, "Chicken")
        for feeder in frame_out.feeders:
            _draw(feeder, "Feeder")
        for drinker in frame_out.drinkers:
            _draw(drinker, "Drinker")

        overlay = vis.copy()
        cv2.rectangle(overlay, (0, 0), (w, 36), (20, 24, 30), -1)
        cv2.addWeighted(overlay, 0.85, vis, 0.15, 0, vis)
        hud = (
            f"FRAME {frame_out.frame_idx} | BIRDS: {len(frame_out.birds)} | "
            f"FEEDERS: {len(frame_out.feeders)} | DRINKERS: {len(frame_out.drinkers)}"
        )
        cv2.putText(vis, hud, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        return vis
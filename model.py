import os
import cv2
import numpy as np
import base64
from collections import Counter, deque
from ultralytics import YOLO
from legband import (
    BAND_CLASS_TO_HEN,
    detect_leg_band_color,
    equipment_boxes_overlap,
    suppress_overlapping_equipment,
)

# Strict display colors requested:
# Rooster -> Red
# Unidentified Chicken -> Orange
# Hens -> Matched to leg band color
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

def get_bbox_color(label: str) -> dict:
    return COLOR_MAP.get(label, COLOR_MAP["Default"])


class ChickenDetector:
    def __init__(self, model_path=None):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir  = os.path.dirname(current_dir)

        custom_candidates = [
            os.path.join(current_dir, "custom_poultry.pt"),
            os.path.join(parent_dir, "custom_poultry.pt")
        ]
        tracker_cand = [
            os.path.join(current_dir, "bytetrack_poultry.yaml"),
            os.path.join(parent_dir, "bytetrack_poultry.yaml")
        ]
        self.tracker_yaml = next((p for p in tracker_cand if os.path.isfile(p)), "bytetrack.yaml")

        if model_path:
            requested_model = os.path.abspath(model_path)
            if not os.path.isfile(requested_model):
                raise FileNotFoundError(f"Model weights not found: {requested_model}")
            print(f"[MODEL] Loading requested model: {requested_model}")
            self.model = YOLO(requested_model)
            self.is_custom = os.path.basename(requested_model).lower() == "custom_poultry.pt"
        else:
            custom_pt = next((p for p in custom_candidates if os.path.isfile(p)), None)
            if custom_pt:
                print(f"[MODEL] Loading custom poultry model: {custom_pt}")
                self.model = YOLO(custom_pt)
                self.is_custom = True
            else:
                fallback_pt = os.path.join(current_dir, "yolo26n.pt")
                print(f"[MODEL] custom_poultry.pt not found — fallback: {fallback_pt}")
                self.model = YOLO(fallback_pt)
                self.is_custom = False

        self.class_names = self.model.names

        self.current_mode     = "accuracy"
        self.frame_count      = 0
        self.position_history = {}
        self.track_states     = {}   # track_id -> {"label": str, "last_seen_frame": int}
        self.track_band_votes = {}
        self.track_band_last_seen = {}
        self.cumulative_count = set()
        self.ghost_tracks     = {}
        self.smoothed_boxes   = {}
        self.short_id_map     = {}
        self._next_short_id   = 1

    def set_mode(self, mode: str):
        self.current_mode = mode
        return mode

    def _record_track_band_label(self, track_id, label: str) -> None:
        if track_id is None:
            return
        last_seen = self.track_band_last_seen.get(track_id)
        votes = self.track_band_votes.setdefault(track_id, deque(maxlen=5))
        if last_seen is not None and self.frame_count - last_seen > 30:
            votes.clear()
        votes.append(label)
        self.track_band_last_seen[track_id] = self.frame_count
        if Counter(votes).most_common(1)[0][1] >= 3:
            self.track_states[track_id] = {
                "label": Counter(votes).most_common(1)[0][0],
                "last_seen_frame": self.frame_count,
            }

    def _prune_track_band_state(self) -> None:
        expired_states = [
            track_id
            for track_id, state in self.track_states.items()
            if self.frame_count - state["last_seen_frame"] > 300
        ]
        expired_votes = [
            track_id
            for track_id, last_seen in self.track_band_last_seen.items()
            if self.frame_count - last_seen > 300
        ]
        for track_id in expired_states:
            self.track_states.pop(track_id, None)
        for track_id in set(expired_states + expired_votes):
            self.track_band_votes.pop(track_id, None)
            self.track_band_last_seen.pop(track_id, None)

    def detect(self, frame):
        self.frame_count += 1
        self._prune_track_band_state()

        if isinstance(frame, str):
            try:
                if "," in frame:
                    frame = frame.split(",")[1]
                img_bytes = base64.b64decode(frame)
                nparr     = np.frombuffer(img_bytes, np.uint8)
                frame     = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            except Exception:
                return self._empty_result()

        if frame is None or not isinstance(frame, np.ndarray):
            return self._empty_result()

        source_frame = frame
        frame_h, frame_w = frame.shape[:2]
        orig_w, orig_h = frame_w, frame_h

        target_width = 640 if self.current_mode == "accuracy" else 320
        TARGET_W = target_width
        if frame_w > TARGET_W:
            scale_r   = TARGET_W / frame_w
            target_h  = int(frame_h * scale_r)
            frame     = cv2.resize(frame, (TARGET_W, target_h), interpolation=cv2.INTER_AREA)
            frame_h, frame_w = frame.shape[:2]

        infer_sz = 320

        try:
            results = self.model.track(
                frame,
                persist=True,
                tracker=self.tracker_yaml,
                verbose=False,
                conf=0.15,
                iou=0.45,
                agnostic_nms=False,
                imgsz=infer_sz,
            )[0]
        except Exception:
            try:
                results = self.model(
                    frame,
                    verbose=False,
                    conf=0.15,
                    iou=0.45,
                    agnostic_nms=False,
                    imgsz=infer_sz,
                )[0]
            except Exception:
                return self._empty_result()

        inference_results = [(results, False)]
        if self.current_mode == "accuracy":
            try:
                high_resolution_result = self.model.predict(
                    frame,
                    verbose=False,
                    conf=0.15,
                    iou=0.45,
                    agnostic_nms=False,
                    imgsz=640,
                )[0]
                inference_results.append((high_resolution_result, True))
            except Exception as error:
                print(f"[MODEL WARN] High-resolution equipment pass failed: {error}")

        if not any(result.boxes is not None and len(result.boxes) for result, _ in inference_results):
            return self._build_result([])

        chicken_dets = []
        rooster_dets = []
        band_dets    = []
        low_resolution_infra = []
        high_resolution_infra = []

        for result, is_high_resolution in inference_results:
            if result.boxes is None or len(result.boxes) == 0:
                continue
            boxes = result.boxes.xyxy.cpu().numpy()
            confs = result.boxes.conf.cpu().numpy()
            cls_ids = result.boxes.cls.int().cpu().tolist()
            track_ids = (
                result.boxes.id.int().cpu().tolist()
                if not is_high_resolution and result.boxes.id is not None
                else [None] * len(boxes)
            )

            for box, conf_val, cls_id, track_id in zip(boxes, confs, cls_ids, track_ids):
                name = self.class_names.get(cls_id, f"class_{cls_id}")
                entry = (box, float(round(conf_val, 2)), name, track_id)

                if is_high_resolution:
                    if name in ("Feeder", "Drinker"):
                        high_resolution_infra.append(entry)
                    continue

                if name == "Chicken":
                    chicken_dets.append(entry)
                elif name == "Rooster":
                    rooster_dets.append(entry)
                elif name.startswith("Leg_Band_"):
                    band_dets.append((box, name))
                elif name in ("Feeder", "Drinker"):
                    low_resolution_infra.append(entry)

        high_resolution_infra = suppress_overlapping_equipment(
            high_resolution_infra
        )
        infra_dets = high_resolution_infra + [
            detection
            for detection in low_resolution_infra
            if not any(
                equipment_boxes_overlap(detection[0], high_detection[0])
                for high_detection in high_resolution_infra
            )
        ]
        infra_dets = suppress_overlapping_equipment(infra_dets)

        # ------------------------------------------------------------------
        # 1. Re-ID ONLY for Hens (Chickens) — Never touch Roosters!
        # ------------------------------------------------------------------
        for (band_box, band_class) in band_dets:
            bx1, by1, bx2, by2 = band_box
            band_cx = (bx1 + bx2) / 2
            band_cy = (by1 + by2) / 2

            for (cbox, _, _, track_id) in chicken_dets:
                if track_id is None:
                    continue

                cx1, cy1, cx2, cy2 = cbox
                # Small expansion to catch leg bands at the bottom edge of body box
                inside_or_near = (cx1 - 10 <= band_cx <= cx2 + 10) and (cy1 - 10 <= band_cy <= cy2 + 10)
                
                if not inside_or_near:
                    continue

                color_name = BAND_CLASS_TO_HEN.get(band_class)
                if not color_name:
                    continue

                self._record_track_band_label(track_id, color_name)
                break

        # ------------------------------------------------------------------
        # 2. Strict HSV Fallback — ONLY run on unidentified Chickens
        # ------------------------------------------------------------------
        scale_x = orig_w / frame_w if frame_w > 0 else 1.0
        scale_y = orig_h / frame_h if frame_h > 0 else 1.0
        for (cbox, _, _, track_id) in chicken_dets:
            if track_id is None or (track_id in self.track_states and self.track_states[track_id]["label"] != "Chicken"):
                continue

            scaled_box = (
                cbox[0] * scale_x,
                cbox[1] * scale_y,
                cbox[2] * scale_x,
                cbox[3] * scale_y,
            )
            band_label = detect_leg_band_color(source_frame, scaled_box)
            if band_label:
                self._record_track_band_label(track_id, band_label)

        # ------------------------------------------------------------------
        # 3. Helpers & Box Output Formatting
        # ------------------------------------------------------------------
        detections = []
        box_sx = orig_w / frame_w if frame_w > 0 else 1.0
        box_sy = orig_h / frame_h if frame_h > 0 else 1.0

        def scale_box(box):
            return [box[0] * box_sx, box[1] * box_sy, box[2] * box_sx, box[3] * box_sy]

        def smooth_box(track_id, raw_box):
            if track_id is None:
                return raw_box
            if track_id not in self.smoothed_boxes:
                self.smoothed_boxes[track_id] = list(raw_box)
                return raw_box
            prev = self.smoothed_boxes[track_id]
            s = [0.4 * r + 0.6 * p for r, p in zip(raw_box, prev)]
            self.smoothed_boxes[track_id] = s
            return s

        live_ids = set()

        def short_id(raw_tid):
            if raw_tid is None:
                return None
            if raw_tid not in self.short_id_map:
                self.short_id_map[raw_tid] = self._next_short_id
                self._next_short_id += 1
            return self.short_id_map[raw_tid]

        # Process Hens / Chickens
        assigned_hen_labels = set()
        for (cbox, conf_val, _, track_id) in chicken_dets:
            raw = scale_box(cbox)
            x1, y1, x2, y2 = smooth_box(track_id, raw)

            if track_id is not None:
                live_ids.add(track_id)
                self.cumulative_count.add(track_id)

            display_label = "Chicken"
            if track_id is not None and track_id in self.track_states:
                cand = self.track_states[track_id]["label"]
                if cand not in assigned_hen_labels:
                    display_label = cand
                    assigned_hen_labels.add(cand)
                self.track_states[track_id]["last_seen_frame"] = self.frame_count

            # Color determined strictly by display_label
            colors = get_bbox_color(display_label)

            det = {
                "box":        [int(x1), int(y1), int(x2), int(y2)],
                "label":      display_label,
                "confidence": conf_val,
                "track_id":   short_id(track_id),
                "type":       "hen",
                "color":      colors["hex"],
                "color_rgb":  colors["rgb"],
                "color_bgr":  colors["bgr"],
            }
            detections.append(det)

        # Process Roosters (ALWAYS forced to "Rooster" and Red box)
        for (cbox, conf_val, _, track_id) in rooster_dets:
            raw = scale_box(cbox)
            x1, y1, x2, y2 = smooth_box(track_id, raw)

            if track_id is not None:
                live_ids.add(track_id)
                self.cumulative_count.add(track_id)

            colors = get_bbox_color("Rooster")

            det = {
                "box":        [int(x1), int(y1), int(x2), int(y2)],
                "label":      "Rooster",
                "confidence": conf_val,
                "track_id":   short_id(track_id),
                "type":       "rooster",
                "color":      colors["hex"],
                "color_rgb":  colors["rgb"],
                "color_bgr":  colors["bgr"],
            }
            detections.append(det)

        # Process Infrastructure
        for (ibox, conf_val, iname, _) in infra_dets:
            x1, y1, x2, y2 = scale_box(ibox)
            colors = get_bbox_color(iname)
            detections.append({
                "box":        [int(x1), int(y1), int(x2), int(y2)],
                "label":      iname,
                "confidence": conf_val,
                "track_id":   None,
                "type":       "infrastructure",
                "color":      colors["hex"],
                "color_rgb":  colors["rgb"],
                "color_bgr":  colors["bgr"],
            })

        return self._build_result(detections)

    def _empty_result(self):
        return {
            "detections":     [],
            "total_count":    len(self.cumulative_count),
            "current_count":  0,
            "mode":           self.current_mode,
            "is_custom":      self.is_custom,
            "tracker":        "ByteTrack",
        }

    def _build_result(self, detections):
        bird_count = sum(1 for d in detections if d.get("type") in ("hen", "rooster"))
        return {
            "detections":     detections,
            "total_count":    len(self.cumulative_count),
            "current_count":  bird_count,
            "mode":           self.current_mode,
            "is_custom":      self.is_custom,
            "tracker":        "ByteTrack",
        }
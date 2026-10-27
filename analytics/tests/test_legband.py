import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from analytics.tracker import LABEL_COLORS_BGR, TrackedDetection, ZampenYOLOTracker
from edge.detector import COLOR_MAP as EDGE_COLOR_MAP
from legband import (
    BAND_CLASS_TO_HEN,
    LEG_BAND_HSV_RANGES,
    detect_leg_band_color,
    suppress_overlapping_equipment,
)
from model import COLOR_MAP as MODEL_COLOR_MAP, ChickenDetector


class TestLegBandDetection(unittest.TestCase):
    def test_blue_band_is_detected_from_original_resolution_crop(self):
        frame = np.zeros((200, 120, 3), dtype=np.uint8)
        frame[150:162, 50:62] = (255, 0, 0)

        label = detect_leg_band_color(frame, (10, 10, 110, 190))

        self.assertEqual(label, "Hen3 (Blue)")

    def test_uncolored_crop_does_not_invent_a_hen_identity(self):
        frame = np.zeros((200, 120, 3), dtype=np.uint8)

        self.assertIsNone(detect_leg_band_color(frame, (10, 10, 110, 190)))

    def test_equipment_box_cannot_be_counted_as_both_feeder_and_drinker(self):
        box = [10, 20, 100, 120]
        detections = [
            (box, 0.4, "Feeder"),
            (box, 0.65, "Drinker"),
        ]

        self.assertEqual(
            suppress_overlapping_equipment(detections),
            [(box, 0.65, "Drinker")],
        )

    def test_checkpoint_violet_band_maps_to_blue_display_identity(self):
        self.assertEqual(BAND_CLASS_TO_HEN["Leg_Band_Violet"], "Hen3 (Blue)")
        self.assertIn("Hen3 (Blue)", LEG_BAND_HSV_RANGES)
        self.assertNotIn("Hen3 (Violet)", LEG_BAND_HSV_RANGES)
        self.assertEqual(MODEL_COLOR_MAP["Hen3 (Blue)"]["hex"], "#0000FF")
        self.assertEqual(EDGE_COLOR_MAP["Hen3 (Blue)"]["hex"], "#0000FF")
        self.assertEqual(LABEL_COLORS_BGR["Hen3 (Blue)"], (255, 0, 0))

    def test_feeder_and_drinker_display_colors_are_gray_in_all_detectors(self):
        for label in ("Feeder", "Drinker"):
            with self.subTest(label=label):
                self.assertEqual(MODEL_COLOR_MAP[label]["hex"], "#808080")
                self.assertEqual(MODEL_COLOR_MAP[label]["rgb"], (128, 128, 128))
                self.assertEqual(MODEL_COLOR_MAP[label]["bgr"], (128, 128, 128))
                self.assertEqual(EDGE_COLOR_MAP[label]["hex"], "#808080")
                self.assertEqual(EDGE_COLOR_MAP[label]["rgb"], (128, 128, 128))
                self.assertEqual(EDGE_COLOR_MAP[label]["bgr"], (128, 128, 128))
                self.assertEqual(LABEL_COLORS_BGR[label], (128, 128, 128))

    def test_analytics_hen_label_requires_consistent_band_observations(self):
        tracker = object.__new__(ZampenYOLOTracker)
        tracker.track_label_cache = {}
        tracker.track_label_votes = {}
        tracker.track_label_last_seen = {}
        tracker.processed_frame_count = 10

        for _ in range(7):
            tracker._record_track_label(12, "Hen3 (Blue)")
        self.assertNotIn(12, tracker.track_label_cache)

        # 8th consistent vote locks the label
        tracker._record_track_label(12, "Hen3 (Blue)")
        self.assertEqual(tracker.track_label_cache[12], "Hen3 (Blue)")

    def test_analytics_track_ids_are_sequential_and_stable(self):
        class FakeTensor:
            def __init__(self, values):
                self.values = np.asarray(values)

            def cpu(self):
                return self

            def numpy(self):
                return self.values

            def int(self):
                return FakeTensor(self.values.astype(int))

            def tolist(self):
                return self.values.tolist()

        class FakeBoxes(SimpleNamespace):
            def __len__(self):
                return len(self.xyxy.values)

        class FakeInternalTracker:
            def __init__(self):
                self.reset_count = 0

            def reset(self):
                self.reset_count += 1

        class FakeModel:
            names = {0: "Chicken"}

            def __init__(self):
                self.confidence_thresholds = []
                self.internal_tracker = FakeInternalTracker()
                self.predictor = SimpleNamespace(trackers=[self.internal_tracker])
                self.results = iter([
                    [SimpleNamespace(boxes=FakeBoxes(
                        xyxy=FakeTensor([[10, 10, 30, 30], [50, 50, 70, 70]]),
                        conf=FakeTensor([0.9, 0.9]),
                        cls=FakeTensor([0, 0]),
                        id=FakeTensor([1000, 1001]),
                    ))],
                    [SimpleNamespace(boxes=FakeBoxes(
                        xyxy=FakeTensor([[50, 50, 70, 70], [10, 10, 30, 30]]),
                        conf=FakeTensor([0.9, 0.9]),
                        cls=FakeTensor([0, 0]),
                        id=FakeTensor([1001, 1000]),
                    ))],
                    [SimpleNamespace(boxes=FakeBoxes(
                        xyxy=FakeTensor([[10, 10, 30, 30], [50, 50, 70, 70]]),
                        conf=FakeTensor([0.9, 0.9]),
                        cls=FakeTensor([0, 0]),
                        id=FakeTensor([1000, 1001]),
                    ))],
                ])

            def track(self, *args, **kwargs):
                self.confidence_thresholds.append(kwargs["conf"])
                return next(self.results)

        tracker = object.__new__(ZampenYOLOTracker)
        tracker.model = FakeModel()
        tracker.tracker_yaml = "bytetrack.yaml"
        tracker.conf = 0.25
        tracker.iou = 0.45
        tracker.imgsz = 640
        tracker.device = "cpu"
        tracker.class_map = {0: "Chicken"}
        tracker.track_label_cache = {}
        tracker.track_label_votes = {}
        tracker.track_label_last_seen = {}
        tracker.track_class_history = {}
        tracker.track_resolved_classes = {}
        tracker.track_class_last_seen = {}
        tracker.processed_frame_count = 0
        tracker.short_id_map = {}
        tracker._next_short_id = 1
        tracker.short_track_positions = {}

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        first = tracker.process_frame(frame, 0)
        second = tracker.process_frame(frame, 1)

        self.assertEqual([bird.track_id for bird in first.birds], [1, 2])
        self.assertEqual([bird.track_id for bird in second.birds], [2, 1])
        self.assertEqual(tracker.model.confidence_thresholds, [0.70, 0.70])
        self.assertIsNone(tracker._resolve_short_id(None))
        self.assertEqual(tracker._next_short_id, 3)

        tracker.reset_video_state()
        self.assertEqual(tracker.model.internal_tracker.reset_count, 1)
        next_video = tracker.process_frame(frame, 0)
        self.assertEqual([bird.track_id for bird in next_video.birds], [1, 2])
        self.assertEqual(tracker._next_short_id, 3)

    def test_tracker_reuses_id_for_nearby_reappearing_chicken(self):
        tracker = object.__new__(ZampenYOLOTracker)
        tracker.short_id_map = {}
        tracker.short_track_positions = {}
        tracker._next_short_id = 1
        tracker.processed_frame_count = 1

        def bird(raw_id, center_x, center_y):
            return TrackedDetection(
                box=[center_x - 10, center_y - 10, center_x + 10, center_y + 10],
                centroid=(center_x, center_y),
                confidence=0.9,
                class_id=0,
                class_name="Chicken",
                track_id=raw_id,
                bird_label="Chicken",
            )

        first = bird(1000, 100, 100)
        tracker._assign_display_ids([first], (360, 640, 3))
        self.assertEqual(first.track_id, 1)

        tracker.processed_frame_count += 5
        reappeared = bird(2000, 130, 120)
        tracker._assign_display_ids([reappeared], (360, 640, 3))
        self.assertEqual(reappeared.track_id, 1)

        tracker.processed_frame_count += 1
        distant = bird(3000, 500, 300)
        tracker._assign_display_ids([distant], (360, 640, 3))
        self.assertEqual(distant.track_id, 2)

        tracker.processed_frame_count += 31
        expired = bird(4000, 500, 300)
        tracker._assign_display_ids([expired], (360, 640, 3))
        self.assertEqual(expired.track_id, 3)

    def test_explicit_model_path_is_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            weights = Path(temporary) / "selected.pt"
            weights.write_bytes(b"model")
            fake_model = type("FakeModel", (), {"names": {0: "Chicken"}})()

            with patch("model.YOLO", return_value=fake_model) as yolo:
                detector = ChickenDetector(str(weights))

        yolo.assert_called_once_with(str(weights.resolve()))
        self.assertIs(detector.model, fake_model)
        self.assertEqual(detector.class_names, {0: "Chicken"})


if __name__ == "__main__":
    unittest.main()

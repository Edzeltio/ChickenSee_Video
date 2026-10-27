import tempfile
import unittest
from pathlib import Path

from train import split_dataset


class TestDatasetPreparation(unittest.TestCase):
    def _add_pair(self, root: Path, source: str, filename: str):
        image = root / "images" / source / filename
        label = root / "labels" / source / f"{Path(filename).stem}.txt"
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"image placeholder")
        label.write_text("0 0.5 0.5 0.25 0.25\n", encoding="utf-8")

    def test_source_recordings_are_kept_together_between_splits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            self._add_pair(root, "video-a", "frame-1.jpg")
            self._add_pair(root, "video-a", "frame-2.jpg")
            self._add_pair(root, "video-b", "frame-1.jpg")

            train_count, val_count = split_dataset(root, seed=4)

            train_groups = {
                path.parent.name for path in (root / "train" / "images").rglob("*.jpg")
            }
            val_groups = {
                path.parent.name for path in (root / "val" / "images").rglob("*.jpg")
            }
            self.assertEqual(train_count + val_count, 3)
            self.assertEqual(len(train_groups), 1)
            self.assertEqual(len(val_groups), 1)
            self.assertTrue(train_groups.isdisjoint(val_groups))

    def test_flat_dataset_requires_explicit_random_split(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            self._add_pair(root, ".", "frame-1.jpg")
            self._add_pair(root, ".", "frame-2.jpg")

            with self.assertRaisesRegex(ValueError, "flat folder"):
                split_dataset(root)

            self.assertEqual(split_dataset(root, random_split=True), (1, 1))

    def test_missing_labels_fail_before_any_split_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            self._add_pair(root, "video-a", "frame-1.jpg")
            (root / "images" / "video-b").mkdir(parents=True)
            (root / "images" / "video-b" / "frame-2.jpg").write_bytes(b"image")

            with self.assertRaisesRegex(ValueError, "Missing YOLO label"):
                split_dataset(root)

            self.assertFalse((root / "train").exists())

    def test_invalid_class_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            self._add_pair(root, "video-a", "frame-1.jpg")
            self._add_pair(root, "video-b", "frame-2.jpg")
            (root / "labels" / "video-a" / "frame-1.txt").write_text(
                "99 0.5 0.5 0.25 0.25\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "class id 99"):
                split_dataset(root)


if __name__ == "__main__":
    unittest.main()

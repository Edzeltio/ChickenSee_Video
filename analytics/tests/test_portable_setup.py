import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from device_utils import resolve_torch_device
from model_registry import (
    POULTRY_CLASS_NAMES,
    activate_model,
    discover_models,
    resolve_model_path,
    validate_poultry_classes,
)


class DeviceSelectionTests(unittest.TestCase):
    @staticmethod
    def fake_torch(cuda=False, mps=False, device_count=1):
        return SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: cuda,
                device_count=lambda: device_count,
            ),
            backends=SimpleNamespace(
                mps=SimpleNamespace(is_available=lambda: mps)
            ),
        )

    def test_auto_selects_cuda_then_mps_then_cpu(self):
        self.assertEqual(resolve_torch_device("auto", self.fake_torch(cuda=True)), "0")
        self.assertEqual(resolve_torch_device("auto", self.fake_torch(mps=True)), "mps")
        self.assertEqual(resolve_torch_device("auto", self.fake_torch()), "cpu")

    def test_explicit_device_requests_are_checked(self):
        self.assertEqual(resolve_torch_device("cuda:1", self.fake_torch(cuda=True, device_count=2)), "1")
        with self.assertRaisesRegex(RuntimeError, "CUDA is unavailable"):
            resolve_torch_device("0", self.fake_torch())
        with self.assertRaisesRegex(RuntimeError, "MPS was requested"):
            resolve_torch_device("mps", self.fake_torch())
        with self.assertRaisesRegex(ValueError, "outside the available device range"):
            resolve_torch_device("2", self.fake_torch(cuda=True, device_count=2))


class ModelRegistryTests(unittest.TestCase):
    def test_activation_and_active_resolution_preserve_versioned_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            models = root / "models"
            models.mkdir()
            first = models / "poultry-v1.pt"
            first.write_bytes(b"first-model")

            manifest = activate_model(first, root)
            self.assertEqual(manifest["path"], "models/poultry-v1.pt")
            self.assertEqual(Path(resolve_model_path(root)), first.resolve())
            self.assertTrue((models / "active.json").is_file())
            self.assertEqual(len(discover_models(root)), 1)
            self.assertEqual(first.read_bytes(), b"first-model")

    def test_explicit_path_precedes_registry_and_missing_active_file_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            active = root / "models" / "poultry-v1.pt"
            active.parent.mkdir()
            active.write_bytes(b"active")
            activate_model(active, root)

            override = root / "override.pt"
            override.write_bytes(b"override")
            self.assertEqual(
                Path(resolve_model_path(root, env_path=str(override))),
                override.resolve(),
            )

            active.unlink()
            with self.assertRaisesRegex(FileNotFoundError, "Active model is missing"):
                resolve_model_path(root)

    def test_legacy_fallback_and_class_order_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fallback = root / "custom_poultry.pt"
            fallback.write_bytes(b"legacy")
            self.assertEqual(Path(resolve_model_path(root)), fallback.resolve())

        validate_poultry_classes(dict(enumerate(POULTRY_CLASS_NAMES)))
        with self.assertRaisesRegex(ValueError, "Incompatible poultry weights"):
            validate_poultry_classes(list(reversed(POULTRY_CLASS_NAMES)))

    def test_latest_version_uses_newest_model_in_models_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            models = root / "models"
            models.mkdir()
            old = models / "poultry-v1.pt"
            new = models / "poultry-v2.pt"
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            os.utime(old, ns=(1_000_000_000, 1_000_000_000))
            os.utime(new, ns=(2_000_000_000, 2_000_000_000))
            self.assertEqual(discover_models(root)[0], new)


if __name__ == "__main__":
    unittest.main()

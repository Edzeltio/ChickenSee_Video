"""Report Python dependencies, automatic inference device, and active model setup."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path

from device_utils import resolve_torch_device
from model_registry import resolve_model_path


PROJECT_ROOT = Path(__file__).resolve().parent
REQUIRED_MODULES = (
    "cv2",
    "numpy",
    "torch",
    "ultralytics",
    "pydantic",
    "sqlalchemy",
    "psutil",
)


def main() -> int:
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Python: {platform.python_version()}")
    failures = []
    if not ((3, 10) <= sys.version_info[:2] < (3, 13)):
        print("UNSUPPORTED Python version; use Python 3.10–3.12.")
        failures.append("Python version")

    for module_name in REQUIRED_MODULES:
        try:
            available = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):
            available = False
        label = "OK" if available else "MISSING"
        print(f"{label:7} {module_name}")
        if not available:
            failures.append(module_name)

    try:
        import torch

        requested = os.getenv("INFERENCE_DEVICE", "auto")
        selected = resolve_torch_device(requested, torch)
        print(f"Requested inference device: {requested}")
        if selected == "cpu":
            print(f"Selected device: CPU ({os.cpu_count() or 1} logical CPUs)")
        elif selected == "mps":
            print("Selected device: Apple MPS")
        else:
            index = int(selected.split(",")[0])
            print(f"Selected device: GPU {selected} ({torch.cuda.get_device_name(index)})")
    except Exception as error:
        print(f"Device selection: ERROR: {error}")
        if "torch" not in failures:
            failures.append("device selection")

    try:
        model_path = Path(
            resolve_model_path(
                PROJECT_ROOT,
                env_path=os.getenv("MODEL_PATH"),
                fallback_path=PROJECT_ROOT / "custom_poultry.pt",
            )
        )
        if model_path.is_file():
            print(f"Configured model: {model_path}")
            print(f"Model size: {model_path.stat().st_size / (1024 * 1024):.1f} MiB")
        else:
            print(f"Model weights missing: {model_path}")
            print("Add a compatible .pt file, then run: python model_manager.py activate <path>")
            failures.append("model weights")
    except Exception as error:
        print(f"Model configuration: ERROR: {error}")
        failures.append("model configuration")

    if failures:
        print("\nSetup check failed: " + ", ".join(dict.fromkeys(failures)))
        print("Install dependencies with: python -m pip install -r analytics/requirements.txt")
        return 1

    print("\nSetup check passed. A running service must be restarted after changing weights.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

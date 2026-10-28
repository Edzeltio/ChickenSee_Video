---
name: Portable model deployment
description: Shared model selection and device behavior across analytics and edge inference.
---

Edge and analytics inference use the same versioned model registry. Keep each `.pt` file under `models/` with a distinct version name; update the active pointer deliberately, preserve prior files, and validate the exact poultry class order before inference. `MODEL_PATH` is an explicit environment override and takes precedence over the registry. Device selection defaults to CUDA/ROCm, then Apple MPS, then CPU when the installed PyTorch build exposes those backends.

**Why:** Selecting whichever checkpoint happens to be present or newest can silently load incompatible classes or break rollback. Hardware capability depends on the locally installed PyTorch build, not only on the machine's GPU.

**How to apply:** Keep model registration shared by edge and analytics, make model changes restart-safe, and document backend-specific PyTorch installation separately from the CPU baseline. Never claim a device backend was tested without running on that hardware.

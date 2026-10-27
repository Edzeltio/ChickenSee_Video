"""Portable PyTorch/Ultralytics device selection."""

from __future__ import annotations

import re
from typing import Any, Optional


def resolve_torch_device(requested: str = "auto", torch_module: Optional[Any] = None) -> str:
    """Resolve a user-friendly device preference to an Ultralytics device string.

    ``auto`` prefers CUDA, then Apple MPS, then CPU. AMD GPUs exposed through
    PyTorch's ROCm build use the CUDA-compatible device API and are handled as
    CUDA devices here.
    """
    preference = str(requested or "auto").strip().lower()
    if preference in ("", "auto"):
        if torch_module is None:
            import torch as torch_module

        cuda = getattr(torch_module, "cuda", None)
        if cuda is not None and cuda.is_available():
            return "0"
        backends = getattr(torch_module, "backends", None)
        mps = getattr(backends, "mps", None) if backends is not None else None
        if mps is not None and mps.is_available():
            return "mps"
        return "cpu"

    if preference == "cpu":
        return "cpu"

    if preference == "cuda":
        preference = "0"
    elif preference.startswith("cuda:"):
        preference = preference.split(":", 1)[1]

    if preference == "mps":
        if torch_module is None:
            import torch as torch_module
        backends = getattr(torch_module, "backends", None)
        mps = getattr(backends, "mps", None) if backends is not None else None
        if mps is None or not mps.is_available():
            raise RuntimeError("MPS was requested but is not available in this PyTorch install.")
        return "mps"

    if not re.fullmatch(r"\d+(,\d+)*", preference):
        raise ValueError(
            f"Unsupported inference device {requested!r}; use auto, cpu, mps, "
            "cuda, or CUDA device indices such as 0 or 0,1."
        )

    if torch_module is None:
        import torch as torch_module
    cuda = getattr(torch_module, "cuda", None)
    if cuda is None or not cuda.is_available():
        raise RuntimeError(
            f"CUDA device {preference!r} was requested but CUDA is unavailable. "
            "Use INFERENCE_DEVICE=auto or cpu, or install a compatible PyTorch GPU build."
        )
    device_count = cuda.device_count()
    indices = [int(value) for value in preference.split(",")]
    if any(index >= device_count for index in indices):
        raise ValueError(
            f"CUDA device {preference!r} is outside the available device range "
            f"(0 through {device_count - 1})."
        )
    return preference

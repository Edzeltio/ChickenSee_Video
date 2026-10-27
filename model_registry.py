"""Small, dependency-free active YOLO weights registry."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


POULTRY_CLASS_NAMES = (
    "Chicken",
    "Drinker",
    "Feeder",
    "Leg_Band_Green",
    "Leg_Band_Pink",
    "Leg_Band_Violet",
    "Leg_Band_White",
    "Leg_Band_Yellow",
    "Rooster",
)


def registry_path(project_root: Path) -> Path:
    return Path(project_root).resolve() / "models" / "active.json"


def _resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_model_path(
    project_root: Path,
    env_path: Optional[str] = None,
    fallback_path: Optional[Path] = None,
) -> str:
    """Resolve explicit env override, active registry, then legacy fallback.

    A broken active-manifest reference raises instead of silently reverting to
    older weights. MODEL_PATH always takes precedence over the registry.
    """
    root = Path(project_root).resolve()
    if env_path:
        return str(_resolve_path(env_path, root))

    manifest = registry_path(root)
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            stored_path = data["path"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f"Invalid active model registry: {manifest}") from error
        if not isinstance(stored_path, str) or not stored_path.strip():
            raise ValueError(f"Active model registry has an empty model path: {manifest}")
        active_model = _resolve_path(stored_path, root)
        if not active_model.is_file():
            raise FileNotFoundError(
                f"Active model is missing: {active_model}. Re-activate an available .pt file."
            )
        return str(active_model)

    if fallback_path is None:
        fallback_path = root / "custom_poultry.pt"
    return str(_resolve_path(str(fallback_path), root))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def activate_model(model_path: Path, project_root: Path) -> Dict[str, Any]:
    """Atomically select an existing .pt file without copying or deleting it."""
    root = Path(project_root).resolve()
    source = Path(model_path).expanduser()
    if not source.is_absolute():
        source = root / source
    source = source.resolve()
    if source.suffix.lower() != ".pt":
        raise ValueError(f"Model file must have a .pt extension: {source}")
    if not source.is_file():
        raise FileNotFoundError(f"Model file does not exist: {source}")
    if source.stat().st_size == 0:
        raise ValueError(f"Model file is empty: {source}")

    try:
        stored_path = source.relative_to(root).as_posix()
    except ValueError:
        stored_path = str(source)

    manifest = registry_path(root)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "path": stored_path,
        "version": source.stem,
        "sha256": sha256_file(source),
        "activated_at": datetime.now(timezone.utc).isoformat(),
    }
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=manifest.parent, delete=False
        ) as temp_file:
            json.dump(data, temp_file, indent=2)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)
        os.replace(temp_path, manifest)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return data


def discover_models(project_root: Path) -> List[Path]:
    """Return local managed weights, newest file first."""
    directory = Path(project_root).resolve() / "models"
    if not directory.is_dir():
        return []
    candidates = [path for path in directory.glob("*.pt") if path.is_file()]
    return sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)


def validate_poultry_classes(names: Any) -> None:
    """Fail clearly when weights use a different class set or ordering."""
    if isinstance(names, dict):
        try:
            actual = [names[index] for index in range(len(names))]
        except (KeyError, TypeError):
            actual = [names[key] for key in sorted(names, key=lambda value: int(value))]
    elif isinstance(names, (list, tuple)):
        actual = list(names)
    else:
        raise ValueError("Model metadata does not expose an ordered class-name list.")

    if actual != list(POULTRY_CLASS_NAMES):
        raise ValueError(
            "Incompatible poultry weights: expected class order "
            f"{list(POULTRY_CLASS_NAMES)}, received {actual}."
        )

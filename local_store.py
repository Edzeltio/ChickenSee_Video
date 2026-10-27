"""Shared local paths for annotated detection videos and result files."""

import os
import json
import shutil
import threading
from pathlib import Path
from datetime import datetime, date
from typing import Any, Optional


def _configured_local_root() -> str:
    fallback = (Path(__file__).resolve().parent / "local-storage").resolve()

    def _try_path(p_str: str) -> Optional[str]:
        try:
            p = Path(p_str).expanduser()
            if not p.is_absolute():
                p = (Path(__file__).resolve().parent / p).resolve()
            p.mkdir(parents=True, exist_ok=True)
            return str(p.resolve())
        except Exception:
            return None

    configured = os.getenv("CHICKENSEE_LOCAL_STORAGE")
    if configured:
        resolved = _try_path(configured)
        if resolved:
            return resolved

    agent_env = Path(__file__).resolve().parent / "agent" / ".env"
    if agent_env.is_file():
        for line in agent_env.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "CHICKENSEE_LOCAL_STORAGE":
                resolved = _try_path(value.strip().strip('"').strip("'"))
                if resolved:
                    return resolved

    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(fallback)


LOCAL_ROOT = Path(_configured_local_root())
VIDEOS_DIR = LOCAL_ROOT / "videos"
RESULTS_DIR = LOCAL_ROOT / "results"
_QUEUE_STATUS_LOCK = threading.Lock()
_QUEUE_HEARTBEAT_INTERVAL_SECONDS = 5


def ensure_dirs() -> None:
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def json_default(obj: Any):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return list(obj)
    return str(obj)


def write_results_json(stem: str, payload: dict) -> Path:
    ensure_dirs()
    path = RESULTS_DIR / f"{stem}_results.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=json_default)
    return path


def write_queue_status_json(payload: dict) -> Path:
    ensure_dirs()
    path = RESULTS_DIR / "queue_status.json"
    with _QUEUE_STATUS_LOCK:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict):
            previous = {key: value for key, value in existing.items() if key != "updated_at"}
            current = {key: value for key, value in payload.items() if key != "updated_at"}
            try:
                updated_at = datetime.fromisoformat(
                    str(existing["updated_at"]).replace("Z", "+00:00")
                )
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=datetime.now().astimezone().tzinfo)
                heartbeat_age = max(0, (
                    datetime.now().astimezone() - updated_at.astimezone()
                ).total_seconds())
            except (KeyError, TypeError, ValueError):
                heartbeat_age = _QUEUE_HEARTBEAT_INTERVAL_SECONDS
            if (
                previous == current
                and 0 <= heartbeat_age < _QUEUE_HEARTBEAT_INTERVAL_SECONDS
            ):
                return path
        _write_json_atomically(path, payload)
    return path


def write_detection_status_json(payload: dict) -> Path:
    ensure_dirs()
    path = RESULTS_DIR / "detection_status.json"
    _write_json_atomically(path, payload)
    return path


def read_detection_status_json() -> Optional[dict]:
    path = RESULTS_DIR / "detection_status.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_live_detection_frame(image_bytes: bytes) -> Path:
    ensure_dirs()
    path = RESULTS_DIR / "live_detection.jpg"
    temp_path = path.with_suffix(".jpg.tmp")
    temp_path.write_bytes(image_bytes)
    os.replace(temp_path, path)
    return path


def _write_json_atomically(path: Path, payload: dict) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=json_default)
    os.replace(temp_path, path)


def update_queue_status_json(payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "queue_status.json"
    with _QUEUE_STATUS_LOCK:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, json.JSONDecodeError):
            existing = {}

        summary = existing.get("summary")
        if not isinstance(summary, dict):
            summary = {status.lower(): 0 for status in (
                "PROCESSING", "QUEUED", "WAITING", "COMPLETED", "FAILED", "CANCELLED"
            )}
        else:
            summary = dict(summary)

        items = existing.get("items")
        items = [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        job_id = payload.get("job_id")
        video_filename = payload.get("video_filename")
        source_item_id = payload.get("source_item_id")
        status = payload.get("status")
        if job_id is not None and status:
            item_id = f"job-{job_id}"
            item_index = next(
                (
                    index for index, item in enumerate(items)
                    if item.get("item_id") in (item_id, source_item_id)
                ),
                None,
            )
            previous_status = items[item_index].get("status") if item_index is not None else None
            item = items[item_index] if item_index is not None else {"item_id": item_id}
            item.update({
                "item_id": item_id,
                "video_filename": video_filename,
                "status": status,
                "progress_percent": payload.get("progress_percent"),
                "frames": payload.get("frames", "—"),
            })
            if item_index is None:
                items.insert(0, item)
            if previous_status != status:
                if previous_status:
                    previous_key = previous_status.lower()
                    summary[previous_key] = max(0, summary.get(previous_key, 0) - 1)
                current_key = status.lower()
                summary[current_key] = summary.get(current_key, 0) + 1

        summary = {
            state: sum(str(item.get("status", "")).lower() == state for item in items)
            for state in ("processing", "queued", "waiting", "completed", "failed", "cancelled")
        }
        active_item = next((item for item in items if item.get("status") == "PROCESSING"), None)
        result = {
            **existing,
            "updated_at": payload.get("updated_at"),
            "is_queueing": sum(summary.get(status, 0) for status in ("processing", "queued", "waiting")) > 0,
            "active_video": active_item.get("video_filename") if active_item else None,
            "active_progress": active_item.get("progress_percent") if active_item else None,
            "active_frames": active_item.get("frames") if active_item else None,
            "active_detection": (
                payload.get("active_detection", existing.get("active_detection"))
                if active_item else None
            ),
            "summary": summary,
            "items": items,
        }
        _write_json_atomically(path, result)
    return path


def copy_video_locally(src: str, dest_name: Optional[str] = None) -> Optional[Path]:
    """Copies a finished video into the local videos folder."""
    if not src or not os.path.isfile(src):
        return None
    ensure_dirs()
    dest = VIDEOS_DIR / (dest_name or Path(src).name)
    shutil.copy2(src, dest)
    return dest


def copy_result_file(src: str) -> Optional[Path]:
    if not src or not os.path.isfile(src):
        return None
    ensure_dirs()
    dest = RESULTS_DIR / Path(src).name
    shutil.copy2(src, dest)
    return dest


ensure_dirs()

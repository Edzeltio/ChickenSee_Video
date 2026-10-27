"""
analytics/api.py
FastAPI REST API wrapper for the ZAMPEN Native Layer Chicken Analytics System.
Exposes endpoints for external front-ends and dashboards:
  - GET /health
  - GET /results/latest
  - GET /results/range?start=...&end=...
  - GET /jobs
  - POST /jobs/process-now
  - POST /detect-sample  ← run YOLO detection on an uploaded video, returns annotated mp4
"""

import os
import json
import hashlib
import shutil
import tempfile
import subprocess
import psutil
import datetime
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from .config import CONFIG
from .database import DB, ProcessingJob, HalfHourMetrics, JobStatus

app = FastAPI(
    title="ZAMPEN Native Layer Chicken Behavior & Sensor Analytics API",
    description="Automatic watched-folder video analysis with interval sensor aggregation using YOLO26n + ByteTrack.",
    version="1.0.0"
)

# Enable CORS for Next.js and external dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    uptime_s: float
    system: Dict[str, Any]
    model: Dict[str, Any]
    storage: Dict[str, Any]


START_TIME = datetime.datetime.utcnow()


@app.get("/health", response_model=HealthResponse, tags=["System Health"])
def get_health():
    """
    Check worker daemon status, CPU/RAM utilization, and model availability on the OptiPlex.
    """
    uptime = (datetime.datetime.utcnow() - START_TIME).total_seconds()
    cpu_pct = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()

    with DB.get_session() as session:
        active_job = session.query(ProcessingJob).filter_by(status=JobStatus.PROCESSING).first()
        completed_count = session.query(ProcessingJob).filter_by(status=JobStatus.COMPLETED).count()
        failed_count = session.query(ProcessingJob).filter_by(status=JobStatus.FAILED).count()

    return HealthResponse(
        status="healthy",
        timestamp=datetime.datetime.utcnow().isoformat() + "Z",
        uptime_s=round(uptime, 1),
        system={
            "cpu_percent": cpu_pct,
            "ram_used_mb": round((ram.total - ram.available) / 1024 / 1024, 1),
            "ram_total_mb": round(ram.total / 1024 / 1024, 1),
            "active_processing_job": active_job.video_filename if active_job else None,
        },
        model={
            "detector": CONFIG.model_name,
            "tracker": CONFIG.tracker_config,
            "device": CONFIG.device,
            "conf_threshold": CONFIG.conf_threshold,
        },
        storage={
            "analytics_db": CONFIG.analytics_db_url,
            "completed_jobs": completed_count,
            "failed_jobs": failed_count,
        }
    )


@app.get("/results/latest", tags=["Analytics Results"])
def get_latest_results():
    """
    Instantly fetch the most recent hourly processed metrics block.
    Returns the latest video's analysis intervals with paired environmental sensor averages and vision behaviors.
    """
    latest = DB.get_latest_metrics()
    if not latest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No processed analytics results found yet. Worker may still be processing initial files."
        )
    return latest


@app.get("/results/range", tags=["Analytics Results"])
def get_results_by_range(
    start: str = Query(..., description="Start timestamp in ISO 8601 (e.g. 2026-10-14T06:00:00)"),
    end: str = Query(..., description="End timestamp in ISO 8601 (e.g. 2026-10-14T18:00:00)")
):
    """
    Query historical 30-minute sensor averages and behavior analytics by timestamp range.
    Splits into 30-minute intervals containing temperature, humidity, ammonia, THI,
    as well as Reduced Eating, Drinking, Inactivity, and Feather Pecking measurements.
    """
    try:
        start_clean = start.replace("Z", "")
        end_clean = end.replace("Z", "")
        start_dt = datetime.datetime.fromisoformat(start_clean)
        end_dt = datetime.datetime.fromisoformat(end_clean)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ISO 8601 timestamp format: {e}. Example: 2026-10-14T06:00:00"
        )

    records = DB.get_metrics_by_range(start_dt, end_dt)
    return {
        "query_range": {
            "start": start,
            "end": end,
            "total_30m_chunks_found": len(records),
        },
        "records": records
    }


@app.get("/jobs", tags=["Job State Manager"])
def list_jobs(limit: int = Query(default=20, le=100)):
    """List recent video analysis jobs and their state."""
    with DB.get_session() as session:
        jobs = session.query(ProcessingJob).order_by(ProcessingJob.id.desc()).limit(limit).all()
        return [
            {
                "id": j.id,
                "video_filename": j.video_filename,
                "date": j.date_slot,
                "hour_slot": j.hour_slot,
                "status": j.status.value,
                "frames_processed": j.frames_processed,
                "progress_frames": j.progress_frames,
                "total_frames": j.total_frames,
                "progress_percent": _progress_percent(j),
                "video_path": j.video_path,
                "processing_time_s": round(j.processing_time_s, 1),
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                "error_message": j.error_message,
            }
            for j in jobs
        ]


def _progress_percent(job: ProcessingJob) -> Optional[float]:
    if job.status == JobStatus.COMPLETED:
        return 100.0
    if not job.total_frames or job.progress_frames is None:
        return None
    progress = max(0, min(job.progress_frames, job.total_frames))
    return round(progress * 100.0 / job.total_frames, 1)


def _queue_video_dirs(directories_file: Optional[Path] = None) -> List[Path]:
    config_file = directories_file or (
        Path(__file__).resolve().parent.parent / "agent" / "directories.json"
    )
    if not config_file.is_file():
        return [Path(CONFIG.video_dir).expanduser().resolve()]
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=500, detail=f"Could not read video watch folders: {error}")
    if not isinstance(config, dict):
        raise HTTPException(status_code=500, detail="Video watch-folder configuration must be an object.")
    configured = config.get("directories", [])
    if not isinstance(configured, list) or not all(isinstance(path, str) for path in configured):
        raise HTTPException(status_code=500, detail="Video watch folders must be a list of paths.")
    directories = {Path(path).expanduser().resolve() for path in configured}
    return sorted(directories, key=str)


@app.get("/queue", tags=["Job State Manager"])
def get_detection_queue(limit: int = Query(default=500, le=500)):
    """Return persisted jobs and stable or still-writing videos in worker discovery order."""
    warnings: List[str] = []
    now = time.time()
    video_dirs = _queue_video_dirs()
    active_video_dirs = [str(directory) for directory in video_dirs if directory.is_dir()]
    missing_video_dirs = [str(directory) for directory in video_dirs if not directory.is_dir()]
    discovered: Dict[str, Dict[str, Any]] = {}
    for video_dir in video_dirs:
        if not video_dir.is_dir():
            continue
        try:
            paths = (
                path for path in video_dir.rglob("*")
                if path.is_file() and path.suffix.casefold() == ".mp4"
            )
            for path in paths:
                if "_detected" in path.stem.lower():
                    continue
                try:
                    resolved = path.resolve()
                    stat = resolved.stat()
                    modified_at = stat.st_mtime
                except OSError as error:
                    warnings.append(f"Could not inspect {path}: {error}")
                    continue
                key = os.path.normcase(str(resolved))
                discovered[key] = {
                    "video_path": str(resolved),
                    "video_filename": resolved.name,
                    "modified_at": modified_at,
                    "signature": (
                        stat.st_size,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                    ),
                    "status": "QUEUED" if now - modified_at >= CONFIG.stability_threshold_s else "WAITING",
                }
        except OSError as error:
            warnings.append(f"Could not scan {video_dir}: {error}")

    with DB.get_session() as session:
        jobs = (
            session.query(ProcessingJob)
            .order_by(ProcessingJob.id.desc())
            .all()
        )
        completed_jobs = [job for job in jobs if job.status == JobStatus.COMPLETED]

    completed_versions = {}
    for job in completed_jobs:
        key = os.path.normcase(os.path.abspath(job.video_path))
        completed_versions.setdefault(key, job)
    job_paths = set()
    job_items = []
    for job in jobs:
        key = os.path.normcase(os.path.abspath(job.video_path))
        job_paths.add(key)
        candidate = discovered.get(key)
        state = job.status.value
        details = job.error_message
        if job.status == JobStatus.FAILED and candidate:
            if DB.file_version_matches(job, candidate["signature"]):
                state = JobStatus.FAILED.value
                details = job.error_message
            else:
                state = candidate["status"]
                details = (
                    "File changed since its previous failed analysis; queued for retry."
                    if state == "QUEUED"
                    else "Waiting for the file to finish writing after a failed analysis."
                )
        elif (
            job.status == JobStatus.COMPLETED
            and candidate
            and not DB.file_version_matches(job, candidate["signature"])
        ):
            state = candidate["status"]
            details = "File changed since its previous analysis; queued for reanalysis."
        job_items.append({
            "item_id": f"job-{job.id}",
            "job_id": job.id,
            "video_filename": job.video_filename,
            "video_path": job.video_path,
            "status": state,
            "progress_frames": job.progress_frames,
            "total_frames": job.total_frames,
            "progress_percent": _progress_percent(job) if state == job.status.value else None,
            "details": details,
            "modified_at": candidate["modified_at"] if candidate else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        })

    discovered_items = [
        {
            "item_id": f"file-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}",
            "video_path": candidate["video_path"],
            "video_filename": candidate["video_filename"],
            "modified_at": candidate["modified_at"],
            "status": candidate["status"],
            "progress_frames": None,
            "total_frames": None,
            "progress_percent": None,
            "details": None,
            "created_at": None,
        }
        for key, candidate in discovered.items()
        if key not in job_paths and (
            key not in completed_versions
            or not DB.file_version_matches(
                completed_versions[key], candidate["signature"]
            )
        )
    ]
    active_items = [item for item in job_items if item["status"] == JobStatus.PROCESSING.value]
    pending_items = [
        item for item in job_items + discovered_items
        if item["status"] in ("QUEUED", "WAITING")
    ]
    pending_items.sort(key=lambda item: item["modified_at"] or now)
    history_items = [
        item for item in job_items
        if item["status"] in (
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        )
    ]
    history_items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    all_items = active_items + pending_items + history_items
    items = all_items[:limit]
    summary = {
        "processing": sum(item["status"] == "PROCESSING" for item in all_items),
        "queued": sum(item["status"] == "QUEUED" for item in all_items),
        "waiting": sum(item["status"] == "WAITING" for item in all_items),
        "completed": sum(item["status"] == "COMPLETED" for item in all_items),
        "failed": sum(item["status"] == "FAILED" for item in all_items),
        "cancelled": sum(item["status"] == "CANCELLED" for item in all_items),
    }
    active_item = active_items[0] if active_items else None
    try:
        from local_store import read_detection_status_json
        detection_status = read_detection_status_json()
    except (OSError, ValueError) as error:
        print(f"[QUEUE WARN] Could not read live detection status: {error}")
        detection_status = None
    active_detection = (
        detection_status
        if active_item
        and detection_status
        and detection_status.get("job_id") == active_item.get("job_id")
        else None
    )

    try:
        from local_store import write_queue_status_json
        write_queue_status_json({
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "is_queueing": (summary["processing"] + summary["queued"] + summary["waiting"]) > 0,
            "active_video": active_item.get("video_filename") if active_item else None,
            "active_progress": active_item.get("progress_percent") if active_item else None,
            "active_frames": (
                f"{active_item.get('progress_frames', 0)} / {active_item.get('total_frames', 0)}"
                if active_item and active_item.get("total_frames")
                else None
            ),
            "active_detection": active_detection,
            "summary": summary,
            "items": [
                {
                    "item_id": it.get("item_id"),
                    "video_filename": it.get("video_filename"),
                    "status": it.get("status"),
                    "progress_percent": it.get("progress_percent"),
                    "frames": (
                        f"{it.get('progress_frames', 0)} / {it.get('total_frames', 0)}"
                        if it.get("total_frames")
                        else "—"
                    ),
                    "details": it.get("details"),
                    "created_at": it.get("created_at"),
                    "completed_at": it.get("completed_at"),
                }
                for it in items
            ],
        })
    except Exception as error:
        warning = f"Could not update queue status snapshot: {error}"
        warnings.append(warning)
        print(f"[QUEUE WARN] {warning}")

    return {
        "items": items,
        "warnings": warnings,
        "watch_directories": active_video_dirs,
        "missing_watch_directories": missing_video_dirs,
        "summary": summary,
        "active_detection": active_detection,
    }


@app.post("/queue/refresh", tags=["Job State Manager"])
def refresh_detection_queue():
    """Trigger an immediate queue scan, persist queue_status.json, and return the latest queue state."""
    return get_detection_queue(limit=100)


@app.get("/queue/live-frame", tags=["Job State Manager"])
def get_live_detection_frame():
    """Return the most recent annotated frame from the active video analysis."""
    from local_store import RESULTS_DIR
    preview_path = RESULTS_DIR / "live_detection.jpg"
    if not preview_path.is_file():
        raise HTTPException(status_code=404, detail="No live detection preview is available.")
    return FileResponse(preview_path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/jobs/trigger", tags=["Job State Manager"])
def trigger_process_file(
    video_filename: str,
    background_tasks: BackgroundTasks,
    force: bool = False
):
    """
    Manually triggers processing of a specific video file in the background.
    """
    video_path = os.path.join(CONFIG.video_dir, video_filename)
    if not os.path.exists(video_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {video_filename} not found in {CONFIG.video_dir}"
        )

    signature = DB.file_signature(video_path)
    if not force and DB.is_file_processed(
        video_filename, video_path, signature
    ):
        return {"message": f"File {video_filename} is already completed.", "status": "COMPLETED"}

    from .worker import ZampenAnalyticsWorker
    worker = ZampenAnalyticsWorker()
    start_dt, end_dt = worker._extract_timestamps_from_file(os.path.PathLike(video_path) if hasattr(os, "PathLike") else datetime.datetime.now())

    def _run_task():
        worker._process_hourly_segment(video_path, video_filename, start_dt, end_dt)

    background_tasks.add_task(_run_task)
    return {
        "message": f"Processing queued for {video_filename}",
        "video_filename": video_filename,
        "status": "QUEUED"
    }


@app.post("/jobs/{job_id}/cancel", tags=["Job State Manager"])
def cancel_processing_job(job_id: int):
    """Request cancellation of an active video analysis."""
    if DB.cancel_processing_job(job_id):
        return {
            "job_id": job_id,
            "status": JobStatus.CANCELLED.value,
            "message": "Cancellation requested. The worker will stop at its next checkpoint.",
        }
    with DB.get_session() as session:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Analysis job {job_id} was not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Analysis job {job_id} is {job.status.value.lower()} and cannot be cancelled.",
        )



@app.post("/detect-sample", tags=["Sample Detection"])
def detect_sample_video(video: UploadFile = File(...)):
    """
    Accept an uploaded video file, run YOLO26n + ByteTrack detection on every frame,
    burn bounding boxes, IDs, leg-band colours, and a live telemetry HUD into the output,
    transcode the result to web-compatible H.264 (avc1), and return it as an mp4 download.

    Called by the Sample Inspector tab in the ChickenSee dashboard.
    Requires the local Python analytics worker to be running (port 8000).
    """
    import cv2
    from .tracker import ZampenYOLOTracker

    tmp_dir = tempfile.mkdtemp(prefix="chickensee_sample_")
    raw_path = os.path.join(tmp_dir, "input.mp4")
    detected_raw = os.path.join(tmp_dir, "detected_raw.mp4")
    detected_h264 = os.path.join(tmp_dir, "detected.mp4")

    def _cleanup():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    try:
        # 1. Save uploaded bytes to disk
        with open(raw_path, "wb") as f:
            shutil.copyfileobj(video.file, f)

        # 2. Probe video metadata
        cap = cv2.VideoCapture(raw_path)
        if not cap.isOpened():
            raise HTTPException(status_code=400, detail="Could not open uploaded video file.")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 360

        # 3. Set up video writer (mp4v staging, then transcode to H.264)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(detected_raw, fourcc, fps, (src_w, src_h))
        if not writer.isOpened():
            cap.release()
            raise HTTPException(status_code=500, detail="Could not open video writer.")

        # 4. Run YOLO + ByteTrack inference on every frame
        tracker = ZampenYOLOTracker()
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frame_out = tracker.process_frame(frame, frame_idx, fps)
            annotated = tracker.annotate_frame(frame, frame_out)
            writer.write(annotated)
            frame_idx += 1

        cap.release()
        writer.release()

        if frame_idx == 0:
            raise HTTPException(status_code=400, detail="Video had no readable frames.")

        # 5. Transcode mp4v → H.264 for browser compatibility
        try:
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg_exe = "ffmpeg"

        cmd = [
            ffmpeg_exe, "-y", "-i", detected_raw,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            detected_h264,
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0 or not os.path.exists(detected_h264) or os.path.getsize(detected_h264) == 0:
            # Serve the raw mp4v file as fallback (some players support it)
            return FileResponse(
                detected_raw,
                media_type="video/mp4",
                filename="detected.mp4",
                background=BackgroundTask(_cleanup),
            )

        # Remove intermediate mp4v file
        if os.path.exists(detected_raw):
            os.remove(detected_raw)

        return FileResponse(
            detected_h264,
            media_type="video/mp4",
            filename="detected.mp4",
            background=BackgroundTask(_cleanup),
        )

    except HTTPException:
        _cleanup()
        raise
    except Exception as exc:
        _cleanup()
        raise HTTPException(status_code=500, detail=f"Sample detection failed: {exc}") from exc


def start_server():
    import uvicorn
    uvicorn.run("analytics.api:app", host=CONFIG.api_host, port=CONFIG.api_port, reload=False)


if __name__ == "__main__":
    start_server()
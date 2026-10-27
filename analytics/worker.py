"""
analytics/worker.py
Core Worker Daemon for ZAMPEN Layer Chicken Video & Sensor Analytics.
Manages watched-folder polling, OpenCV video decoding, YOLO26 Nano + ByteTrack inference,
interval sensor aggregation, and behavior metric persistence.
"""

import os
import json
import hashlib
import re
import sys
import time
import sqlite3
import datetime
import traceback
import threading
from contextlib import closing
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import cv2

from .config import CONFIG
from .database import DB, JobStatus
from .tracker import ZampenYOLOTracker
from .behavior_engine import BehaviorAnalysisEngine, BehaviorChunkSummary

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from local_store import (
    VIDEOS_DIR,
    write_detection_status_json,
    write_live_detection_frame,
    write_results_json,
)


class AnalysisCancelled(Exception):
    """Raised when the user cancels the active video analysis."""


class ZampenAnalyticsWorker:
    """
    Automated background worker running on the Dell OptiPlex server.
    Monitors the agent's configured video directories, pairs sensor data,
    produces configurable time-chunked behavior and environmental intelligence,
    and saves annotated detection videos and reports locally.
    """

    def __init__(self):
        self.cfg = CONFIG
        self.tracker = ZampenYOLOTracker()
        self.behavior_engine = BehaviorAnalysisEngine()
        self.running = True
        self.current_job_id: Optional[int] = None
        self.current_progress_pct: float = 0.0
        self._fs_event = threading.Event()
        self._fs_stop_event = threading.Event()
        self._watcher_thread: Optional[threading.Thread] = None
        self._queue_snapshot_stop_event = threading.Event()
        self._queue_snapshot_thread: Optional[threading.Thread] = None
        
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        self._start_filesystem_watcher()

    def _start_filesystem_watcher(self):
        """Starts real-time filesystem watcher for all watched video directories."""
        def _watch_target_dirs():
            try:
                from watchfiles import watch
                while not self._fs_stop_event.is_set():
                    dirs = [str(d) for d in self._video_input_dirs() if d.is_dir()]
                    if not dirs:
                        time.sleep(1)
                        continue
                    try:
                        for changes in watch(*dirs, recursive=True, stop_event=self._fs_stop_event, rust_timeout=500):
                            has_video = any(
                                path.lower().endswith(".mp4") and "_detected" not in path.lower()
                                for _, path in changes
                            )
                            if has_video:
                                self._fs_event.set()
                    except Exception:
                        time.sleep(1)
            except Exception as error:
                print(f"[WORKER WARN] Filesystem watcher failed: {error}")

        self._watcher_thread = threading.Thread(target=_watch_target_dirs, daemon=True)
        self._watcher_thread.start()

    def trigger_scan(self):
        """Wakes the worker loop immediately to check for newly arrived videos."""
        self._fs_event.set()

    def start_daemon(self):
        """Main polling loop running continuously as an OptiPlex background service."""
        self._publish_queue_status_snapshot()
        self._queue_snapshot_thread = threading.Thread(
            target=self._publish_queue_status_periodically,
            daemon=True,
        )
        self._queue_snapshot_thread.start()
        print("═══════════════════════════════════════════════════════════════")
        print("  ZAMPEN Layer Chicken Analytics Pipeline - OptiPlex Daemon")
        print("  Model: YOLO26 Nano (yolo26n.pt) + ByteTrack")
        print(f"  Input Video Dirs: {', '.join(str(p) for p in self._video_input_dirs())}")
        print(f"  Local Results   : {VIDEOS_DIR.parent}")
        print(f"  Detected Videos : {VIDEOS_DIR}")
        print(f"  Sensor DB       : {self.cfg.sensor_db_url}")
        print(f"  Analytics DB    : {self.cfg.analytics_db_url}")
        print(f"  File stability  : {self.cfg.stability_threshold_s}s (Real-Time) | Analysis chunks: {self.cfg.chunk_minutes}m")
        print("═══════════════════════════════════════════════════════════════")

        while self.running:
            try:
                candidate = self._find_next_completed_hourly_video()
                if candidate:
                    filepath, filename, start_dt, end_dt = candidate
                    print(f"\n[DAEMON] Discovered stable video for processing: {filename}")
                    print(f"        Window: {start_dt.strftime('%Y-%m-%d %H:%M')} → {end_dt.strftime('%H:%M')}")
                    self._process_hourly_segment(filepath, filename, start_dt, end_dt)
                else:
                    self._fs_event.wait(timeout=self.cfg.poll_interval_s)
                    self._fs_event.clear()
            except KeyboardInterrupt:
                print("\n[DAEMON] Shutdown requested by user.")
                self.running = False
                self._fs_stop_event.set()
                self._fs_event.set()
                break
            except Exception as e:
                print(f"[DAEMON ERROR] Unexpected loop exception: {e}")
                traceback.print_exc()
                time.sleep(2)
        self._queue_snapshot_stop_event.set()
        if self._queue_snapshot_thread:
            self._queue_snapshot_thread.join(timeout=3)

    def _publish_queue_status_periodically(self) -> None:
        while self.running and not self._queue_snapshot_stop_event.is_set():
            self._queue_snapshot_stop_event.wait(timeout=3)
            if self.running and not self._queue_snapshot_stop_event.is_set():
                self._publish_queue_status_snapshot()

    @staticmethod
    def _publish_queue_status_snapshot() -> None:
        try:
            from .api import get_detection_queue
            response = get_detection_queue(limit=500)
            warnings = response.get("warnings", [])
            if warnings:
                print(f"[QUEUE WARN] Queue snapshot warnings: {'; '.join(warnings)}")
        except Exception as error:
            print(f"[QUEUE WARN] Could not publish current queue snapshot: {error}")

    def _video_input_dirs(self, directories_file: Optional[Path] = None) -> List[Path]:
        project_root = Path(__file__).resolve().parent.parent
        cfg = getattr(self, "cfg", CONFIG)
        default_dir = Path(cfg.video_dir).expanduser().resolve()
        config_file = directories_file or (
            project_root / "agent" / "directories.json"
        )
        if not config_file.is_file():
            return [default_dir]
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("directory configuration must be an object")
            configured = config.get("directories", [])
            if not isinstance(configured, list) or not all(isinstance(path, str) for path in configured):
                raise ValueError("directories must be a list of paths")

            directories = set()
            for path in configured:
                p_norm = path.replace("\\", "/")
                if p_norm.endswith("/agent/data") and not Path(path).is_dir():
                    directories.add((project_root / "agent" / "data").resolve())
                else:
                    directories.add(Path(path).expanduser().resolve())

            if not directories:
                directories.add(default_dir)
            return sorted(directories, key=str)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"[WORKER WARN] Could not read configured watch directories: {error}")
            return [default_dir]

    @staticmethod
    def _chunk_frame_ranges(total_frames: int, fps: float, chunk_minutes: int) -> List[Tuple[int, int]]:
        chunk_frames = max(1, round(max(fps, 0.001) * max(1, chunk_minutes) * 60))
        return [
            (first_frame, min(first_frame + chunk_frames, total_frames))
            for first_frame in range(0, max(0, total_frames), chunk_frames)
        ]

    def _find_next_completed_hourly_video(self) -> Optional[Tuple[str, str, datetime.datetime, datetime.datetime]]:
        """Finds the oldest stable MP4 from the configured agent folders."""
        mp4_files = []
        for video_dir in self._video_input_dirs():
            if video_dir.is_dir():
                mp4_files.extend(
                    path for path in video_dir.rglob("*")
                    if path.is_file() and path.suffix.casefold() == ".mp4"
                )

        mp4_files.sort(key=lambda path: path.stat().st_mtime)

        for p in mp4_files:
            filename = p.name
            stem = p.stem.lower()
            # Skip videos we already wrote as annotated output
            if stem.endswith("_detected") or "_detected" in stem:
                continue

            # Skip if already processed in DB
            filepath = str(p.resolve())
            signature = DB.file_signature(filepath)
            if DB.is_file_processed(
                filepath,
                filepath=filepath,
                signature=signature,
            ):
                continue
            if DB.has_failed_file_version(
                filepath, signature
            ):
                continue

            # Ensure file is stable and not actively being written
            file_mtime = p.stat().st_mtime
            age_s = time.time() - file_mtime
            if age_s < self.cfg.stability_threshold_s:
                continue

            # Verify file can be read and is not locked by a camera or writer process
            try:
                with open(p, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    if f.tell() == 0:
                        continue
            except (OSError, PermissionError):
                continue

            # Parse start and end timestamps from filename or modification time
            start_dt, end_dt = self._extract_timestamps_from_file(p)

            return str(p), filename, start_dt, end_dt

        return None

    _REC_FOLDER_RE = re.compile(
        r"rec_(\d{4})\s*-\s*(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})_(\d+)"
    )

    def _extract_timestamps_from_file(self, path: Path) -> Tuple[datetime.datetime, datetime.datetime]:
        """
        Parses video start and end datetime from filename or parent folder conventions.

        Checked in order:
        1. Filename: detection_YYYY-MM-DD_HHMM
        2. Any ancestor folder name: rec_YYYY - MM-DD_HH-MM-SS_ffffff
           (structure: watched_dir / cam_folder / rec_<timestamp> / video.mp4)
        3. Filename: YYYYMMDD_HHMMSS
        4. Fallback: file modification time
        """
        fname = path.name

        # Pattern 1: detection_YYYY-MM-DD_HHMM  (in filename)
        m1 = re.search(r"detection_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})", fname)
        if m1:
            date_s, hh, mm = m1.groups()
            start_dt = datetime.datetime.strptime(f"{date_s} {hh}:{mm}:00", "%Y-%m-%d %H:%M:%S")
            end_dt = start_dt + datetime.timedelta(hours=1)
            return start_dt, end_dt

        # Pattern 2: rec_YYYY - MM-DD_HH-MM-SS_ffffff  (in a parent folder name)
        # Structure: watched_dir / cam_folder / rec_<timestamp>/ / video.mp4
        # Walk parents from immediate parent upward, stop at filesystem root.
        for part in path.parts[-2::-1]:  # skip the filename itself, walk parents inward→outward
            m2 = self._REC_FOLDER_RE.search(part)
            if m2:
                y, mo, d, hh, mm, ss, _us = m2.groups()
                start_dt = datetime.datetime.strptime(
                    f"{y}-{mo}-{d} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S"
                )
                end_dt = start_dt + datetime.timedelta(hours=1)
                return start_dt, end_dt

        # Pattern 3: YYYYMMDD_HHMMSS  (in filename)
        m3 = re.search(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", fname)
        if m3:
            y, mo, d, hh, mm, ss = m3.groups()
            start_dt = datetime.datetime.strptime(f"{y}-{mo}-{d} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S")
            end_dt = start_dt + datetime.timedelta(hours=1)
            return start_dt, end_dt

        # Fallback: based on file modification timestamp
        mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        return mtime, mtime

    @staticmethod
    def _find_paired_sensor_db(video_path: str) -> Optional[Path]:
        """
        Looks for a paired sensor SQLite file inside the same session folder as the video.

        Structure: watched_dir / cam_folder / rec_<timestamp> / video.mp4
                                                              \\ sensors.db   ← paired here

        Prefers a same-basename .db/.sqlite file, or the only SQLite file in the folder.
        Returns the first match found, or None if the session folder contains no DB file.
        """
        session_dir = Path(video_path).parent
        video_stem = Path(video_path).stem.casefold()
        try:
            candidates = sorted(
                path for path in session_dir.iterdir()
                if path.is_file() and path.suffix.casefold() in (".db", ".sqlite", ".sqlite3")
            )
        except OSError as error:
            print(f"[WORKER WARN] Could not inspect paired sensor databases in {session_dir}: {error}")
            return None
        paired = next((path for path in candidates if path.stem.casefold() == video_stem), None)
        if paired is None and len(candidates) == 1:
            paired = candidates[0]
        if paired is not None:
            print(f"[WORKER] Paired sensor DB found: {paired}")
        return paired

    def _pull_sensor_averages_for_range(
        self,
        chunk_start: datetime.datetime,
        chunk_end: datetime.datetime,
        paired_db: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Queries raw sensor records from a SQLite database for the analysis interval
        and computes arithmetic averages, extrema, THI, and heat stress alerts.

        Priority:
          1. paired_db — the session-local sensor file found next to the video
          2. self.cfg.sensor_db_url — the globally configured sensor database
        """
        sensor_db = paired_db if paired_db is not None else Path(self.cfg.sensor_db_url.replace("sqlite:///", ""))
        defaults = {
            "temp_avg_c": None,
            "temp_min_c": None,
            "temp_max_c": None,
            "humidity_avg_pct": None,
            "humidity_min_pct": None,
            "humidity_max_pct": None,
            "ammonia_ppm_avg": None,
            "ammonia_ppm_max": None,
            "lux_avg": None,
            "thi_avg": None,
            "heat_stress_level": "Unavailable",
            "sensor_data_available": False,
        }

        if not sensor_db.exists():
            return defaults

        try:
            start_iso = chunk_start.strftime("%Y-%m-%d %H:%M:%S")
            end_iso = chunk_end.strftime("%Y-%m-%d %H:%M:%S")

            with closing(sqlite3.connect(sensor_db)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = {r["name"] for r in cursor.fetchall()}

                row = None
                calculated_thi = None
                lux_val = None

                if "environmental_logs_raw" in tables:
                    cursor.execute("""
                        SELECT
                            AVG(temperature_c) as t_avg, MIN(temperature_c) as t_min, MAX(temperature_c) as t_max,
                            AVG(humidity_pct) as h_avg, MIN(humidity_pct) as h_min, MAX(humidity_pct) as h_max,
                            AVG(ammonia_ppm) as nh3_avg, MAX(ammonia_ppm) as nh3_max,
                            AVG(lux) as lux_avg, AVG(thi) as thi_avg
                        FROM environmental_logs_raw
                        WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) <= datetime(?)
                    """, (start_iso, end_iso))
                    candidate_row = cursor.fetchone()
                    if candidate_row and any(candidate_row[key] is not None for key in ("t_avg", "h_avg", "nh3_avg", "lux_avg", "thi_avg")):
                        row = candidate_row
                        if row["thi_avg"] is not None:
                            calculated_thi = float(row["thi_avg"])
                        if row["lux_avg"] is not None:
                            lux_val = float(row["lux_avg"])

                elif "sensor_logs" in tables:
                    recording_start = None
                    if "recording" in tables:
                        recording = cursor.execute(
                            "SELECT started_at FROM recording ORDER BY id LIMIT 1"
                        ).fetchone()
                        if recording and recording["started_at"]:
                            recording_start = datetime.datetime.fromisoformat(
                                recording["started_at"].replace("Z", "+00:00")
                            )
                            if recording_start.tzinfo is not None:
                                recording_start = recording_start.astimezone().replace(tzinfo=None)

                    if recording_start is not None:
                        elapsed_start = max(0.0, (chunk_start - recording_start).total_seconds())
                        elapsed_end = max(0.0, (chunk_end - recording_start).total_seconds())
                        cursor.execute("""
                            SELECT
                                AVG(temperature) as t_avg, MIN(temperature) as t_min, MAX(temperature) as t_max,
                                AVG(humidity) as h_avg, MIN(humidity) as h_min, MAX(humidity) as h_max,
                                AVG(ammonia_ppm) as nh3_avg, MAX(ammonia_ppm) as nh3_max
                            FROM sensor_logs
                            WHERE elapsed_seconds >= ? AND elapsed_seconds <= ?
                        """, (elapsed_start, elapsed_end))
                    else:
                        chunk_start_utc = chunk_start.astimezone(datetime.timezone.utc).isoformat()
                        chunk_end_utc = chunk_end.astimezone(datetime.timezone.utc).isoformat()
                        cursor.execute("""
                            SELECT
                                AVG(temperature) as t_avg, MIN(temperature) as t_min, MAX(temperature) as t_max,
                                AVG(humidity) as h_avg, MIN(humidity) as h_min, MAX(humidity) as h_max,
                                AVG(ammonia_ppm) as nh3_avg, MAX(ammonia_ppm) as nh3_max
                            FROM sensor_logs
                            WHERE datetime(captured_at) >= datetime(?) AND datetime(captured_at) <= datetime(?)
                        """, (chunk_start_utc, chunk_end_utc))
                    row = cursor.fetchone()

                    if row and row["t_avg"] is not None and row["h_avg"] is not None:
                        t = float(row["t_avg"])
                        h = float(row["h_avg"])
                        calculated_thi = 0.8 * t + (h / 100.0) * (t - 14.4) + 46.4

                if row and any(row[key] is not None for key in ("t_avg", "h_avg", "nh3_avg")):
                    thi = calculated_thi
                    if thi is None:
                        heat_level = "Unavailable"
                    elif thi >= 84.0:
                        heat_level = "Emergency"
                    elif thi >= 79.0:
                        heat_level = "Severe"
                    elif thi >= 74.0:
                        heat_level = "Moderate"
                    else:
                        heat_level = "Normal"

                    return {
                        "temp_avg_c": round(float(row["t_avg"]), 2) if row["t_avg"] is not None else None,
                        "temp_min_c": round(float(row["t_min"]), 2) if row["t_min"] is not None else None,
                        "temp_max_c": round(float(row["t_max"]), 2) if row["t_max"] is not None else None,
                        "humidity_avg_pct": round(float(row["h_avg"]), 1) if row["h_avg"] is not None else None,
                        "humidity_min_pct": round(float(row["h_min"]), 1) if row["h_min"] is not None else None,
                        "humidity_max_pct": round(float(row["h_max"]), 1) if row["h_max"] is not None else None,
                        "ammonia_ppm_avg": round(float(row["nh3_avg"]), 6) if row["nh3_avg"] is not None else None,
                        "ammonia_ppm_max": round(float(row["nh3_max"]), 6) if row["nh3_max"] is not None else None,
                        "lux_avg": round(lux_val, 1) if lux_val is not None else None,
                        "thi_avg": round(thi, 2) if thi is not None else None,
                        "heat_stress_level": heat_level,
                        "sensor_data_available": True,
                    }
        except Exception as e:
            print(f"[WORKER SENSOR WARN] Failed to query sensor DB: {e}")

        return defaults

    def _process_hourly_segment(
        self,
        video_path: str,
        filename: str,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime,
    ):
        """
        Executes frame-by-frame YOLO26 Nano + ByteTrack inference, splits the video
        into configured intervals, pairs sensor data, commits to analytics database,
        and saves the annotated detection video plus JSON results locally.
        """
        file_signature = DB.file_signature(video_path)
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            err = f"Failed to open video file via OpenCV: {video_path}"
            print(f"[WORKER ERROR] {err}")
            job = DB.lease_job(
                filename, video_path, start_dt, end_dt, signature=file_signature
            )
            if job:
                DB.mark_job_failed(job.id, err)
            cap.release()
            return

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
        if total_frames > 1 and fps > 0:
            duration_s = total_frames / fps
            if start_dt == end_dt:
                start_dt = datetime.datetime.fromtimestamp(Path(video_path).stat().st_mtime) - datetime.timedelta(seconds=duration_s)
            end_dt = start_dt + datetime.timedelta(seconds=duration_s)

        job = DB.lease_job(
            filename, video_path, start_dt, end_dt, signature=file_signature
        )
        if not job:
            print(f"[WORKER] Skipping {filename} (already completed or active).")
            cap.release()
            return

        self.current_job_id = job.id
        self.current_progress_pct = 0.0
        self.tracker.reset_video_state()
        DB.update_job_progress(job.id, 0, total_frames)
        self._update_persisted_queue_status(
            job.id, filename, 0.0, 0, total_frames, "PROCESSING", video_path
        )
        t_start_processing = time.time()
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 360
        stride = max(1, self.cfg.frame_stride)
        writer, detected_path, staging_path = self._open_detected_writer(
            filename, src_w, src_h, fps, stride
        )
        if writer is None or detected_path is None or staging_path is None:
            cap.release()
            error = f"Could not create annotated detection video for {filename}"
            DB.mark_job_failed(job.id, error)
            self._update_persisted_queue_status(
                job.id, filename, None, 0, total_frames, "FAILED", video_path
            )
            self.current_job_id = None
            raise RuntimeError(error)

        paired_db = self._find_paired_sensor_db(video_path)
        write_detection_status_json({
            "job_id": job.id,
            "video_filename": filename,
            "status": "PROCESSING",
            "frame_idx": 0,
            "total_frames": total_frames,
            "bird_count": 0,
            "identified_count": 0,
            "rooster_count": 0,
            "paired_sensor_database": paired_db.name if paired_db else None,
            "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
        })

        try:
            frames_processed, elapsed_s = self._run_chunked_inference(
                cap=cap,
                writer=writer,
                detected_path=detected_path,
                video_path=video_path,
                filename=filename,
                start_dt=start_dt,
                end_dt=end_dt,
                job=job,
                t_start_processing=t_start_processing,
                total_frames=total_frames,
                fps=fps,
                stride=stride,
                paired_db=paired_db,
            )
            self._raise_if_cancelled(job.id)
            writer.release()
            writer = None
            try:
                import imageio_ffmpeg, subprocess
                ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                h264_tmp = staging_path + ".h264.mp4"
                cmd = [ffmpeg_exe, "-y", "-i", staging_path, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", h264_tmp]
                res = subprocess.run(cmd, capture_output=True)
                if res.returncode == 0 and os.path.exists(h264_tmp) and os.path.getsize(h264_tmp) > 0:
                    os.replace(h264_tmp, staging_path)
                elif os.path.exists(h264_tmp):
                    os.remove(h264_tmp)
            except Exception as error:
                print(f"[WORKER WARN] H.264 transcode failed for {filename}: {error}")
            os.replace(staging_path, detected_path)
            DB.mark_job_completed(job.id, frames_processed, total_frames, elapsed_s)
            self._update_persisted_queue_status(
                job.id, filename, 100.0, total_frames, total_frames, "COMPLETED", video_path
            )
            write_detection_status_json({
                "job_id": job.id,
                "video_filename": filename,
                "status": "COMPLETED",
                "paired_sensor_database": paired_db.name if paired_db else None,
                "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
            })
        except AnalysisCancelled:
            write_detection_status_json({
                "job_id": job.id,
                "video_filename": filename,
                "status": "CANCELLED",
                "paired_sensor_database": paired_db.name if paired_db else None,
                "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
            })
            DB.cancel_processing_job(job.id)
            self._update_persisted_queue_status(
                job.id, filename, None, 0, total_frames, "CANCELLED", video_path
            )
            print(f"[WORKER] Analysis cancelled by user: {filename}")
        except Exception as error:
            write_detection_status_json({
                "job_id": job.id,
                "video_filename": filename,
                "status": "FAILED",
                "error": str(error),
                "paired_sensor_database": paired_db.name if paired_db else None,
                "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
            })
            DB.mark_job_failed(job.id, str(error))
            self._update_persisted_queue_status(
                job.id, filename, None, 0, total_frames, "FAILED", video_path
            )
            raise
        finally:
            if cap is not None:
                cap.release()
            if writer is not None:
                writer.release()
            if staging_path and os.path.isfile(staging_path):
                try:
                    os.remove(staging_path)
                except OSError as error:
                    print(f"[WORKER WARN] Could not remove incomplete detection video: {error}")
            if os.path.isfile(detected_path):
                sz_mb = os.path.getsize(detected_path) / 1024 / 1024
                print(f"[LOCAL STORE] Saved annotated detection video: {detected_path} ({sz_mb:.2f} MB)")
            self.current_job_id = None

        self.current_progress_pct = 100.0

    @staticmethod
    def _raise_if_cancelled(job_id: int) -> None:
        if DB.is_job_cancelled(job_id):
            raise AnalysisCancelled("Video analysis was cancelled by the user.")

    def _update_persisted_queue_status(
        self,
        job_id: Optional[int],
        filename: Optional[str],
        pct: Optional[float],
        frame_idx: int,
        total_frames: int,
        status_str: str,
        video_path: str,
    ) -> None:
        try:
            from local_store import read_detection_status_json, update_queue_status_json
            is_proc = status_str == "PROCESSING"
            detection_status = read_detection_status_json()
            active_detection = (
                detection_status
                if is_proc and detection_status and detection_status.get("job_id") == job_id
                else None
            )
            update_queue_status_json({
                "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
                "job_id": job_id,
                "video_filename": filename,
                "source_item_id": f"file-{hashlib.sha256(os.path.normcase(os.path.abspath(video_path)).encode('utf-8')).hexdigest()[:20]}",
                "status": status_str,
                "progress_percent": round(pct, 1) if (is_proc and pct is not None) else None,
                "frames": f"{frame_idx} / {total_frames}" if is_proc else "—",
                "active_detection": active_detection,
            })
        except Exception as error:
            print(f"[QUEUE WARN] Could not update queue status for {filename}: {error}")

    def _run_chunked_inference(
        self,
        cap,
        writer,
        detected_path,
        video_path,
        filename,
        start_dt,
        end_dt,
        job,
        t_start_processing,
        total_frames,
        fps,
        stride,
        paired_db: Optional[Path] = None,
    ):
        fps = fps if fps > 0 else 20.0
        frame_ranges = self._chunk_frame_ranges(total_frames, fps, self.cfg.chunk_minutes)
        chunk_metrics_results = []
        chunk_bird_records = []
        chunk_summaries = []
        frame_idx = 0
        frames_processed = 0
        dt = stride / fps
        video_ended = False

        print(f"[WORKER] Video: {filename} | Total Frames: {total_frames} @ {fps:.1f} FPS")

        for chunk_index, (first_frame, last_frame) in enumerate(frame_ranges, start=1):
            chunk_start = start_dt + datetime.timedelta(seconds=first_frame / fps)
            chunk_end = min(end_dt, start_dt + datetime.timedelta(seconds=last_frame / fps))
            self.behavior_engine.reset_chunk()
            print(
                f"[WORKER] >>> Processing interval {chunk_index}: "
                f"{chunk_start.strftime('%Y-%m-%d %H:%M:%S')} – {chunk_end.strftime('%H:%M:%S')}"
            )

            while self.running and frame_idx < last_frame:
                if frame_idx % 25 == 0:
                    self._raise_if_cancelled(job.id)
                ret, frame = cap.read()
                if not ret:
                    video_ended = True
                    break

                frame_out = None
                annotated = None
                if frame_idx % stride == 0:
                    frame_out = self.tracker.process_frame(frame, frame_idx, fps)
                    self.behavior_engine.process_frame(frame_out, dt)
                    frames_processed += 1
                    if writer is not None:
                        annotated = self.tracker.annotate_frame(frame, frame_out)
                        writer.write(annotated)

                frame_idx += 1
                if frame_out is not None and (
                    frames_processed == 1 or frames_processed % 25 == 0
                ):
                    self.current_progress_pct = (frame_idx / total_frames) * 100.0
                    DB.update_job_progress(job.id, frame_idx, total_frames)
                    bird_count = len(frame_out.birds)
                    identified_count = sum(
                        bird.bird_label != "Chicken" for bird in frame_out.birds
                    )
                    rooster_count = sum(
                        bird.class_name == "Rooster" for bird in frame_out.birds
                    )
                    paired_name = paired_db.name if paired_db else None
                    captured_at = datetime.datetime.utcnow().isoformat() + "Z"
                    write_detection_status_json({
                        "job_id": job.id,
                        "video_filename": filename,
                        "status": "PROCESSING",
                        "frame_idx": frame_idx,
                        "total_frames": total_frames,
                        "bird_count": bird_count,
                        "identified_count": identified_count,
                        "rooster_count": rooster_count,
                        "paired_sensor_database": paired_name,
                        "captured_at": captured_at,
                    })
                    self._update_persisted_queue_status(
                        job.id, filename, self.current_progress_pct, frame_idx,
                        total_frames, "PROCESSING", video_path
                    )
                    if annotated is not None:
                        try:
                            preview_width = min(640, annotated.shape[1])
                            preview_height = max(
                                1,
                                round(annotated.shape[0] * preview_width / annotated.shape[1]),
                            )
                            preview = cv2.resize(
                                annotated,
                                (preview_width, preview_height),
                                interpolation=cv2.INTER_AREA,
                            )
                            encoded, image_buffer = cv2.imencode(
                                ".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 70]
                            )
                            if encoded:
                                write_live_detection_frame(image_buffer.tobytes())
                            else:
                                print("[WORKER WARN] Could not encode live detection preview as JPEG.")
                        except (OSError, cv2.error) as error:
                            print(f"[WORKER WARN] Could not update live detection preview: {error}")
                if frame_idx % 200 == 0:
                    print(f"[PROGRESS] Frame {frame_idx}/{total_frames} ({self.current_progress_pct:.1f}%)")

            if not self.running:
                raise RuntimeError("Video analysis was stopped before completion")
            self._raise_if_cancelled(job.id)
            DB.update_job_progress(job.id, frame_idx, total_frames)

            actual_chunk_end = start_dt + datetime.timedelta(seconds=frame_idx / fps) if video_ended else chunk_end
            summary = self.behavior_engine.compute_chunk_summary()
            sensors = self._pull_sensor_averages_for_range(chunk_start, actual_chunk_end, paired_db=paired_db)
            chunk_record = {
                "date_slot": chunk_start.strftime("%Y-%m-%d"),
                "hour_slot": chunk_start.strftime("%H:00"),
                "chunk_index": chunk_index,
                "chunk_start": chunk_start,
                "chunk_end": actual_chunk_end,
                **sensors,
                "unique_birds_tracked": summary.unique_birds,
                "avg_birds_in_frame": summary.avg_birds_present,
                "flock_mobility_px_s": summary.flock_mobility_px_s,
                "feeding_time_total_s": summary.feeding_time_total_s,
                "feeding_bouts": summary.feeding_bouts,
                "reduced_eating_events": summary.reduced_eating_events,
                "reduced_eating_index": summary.reduced_eating_index,
                "drinking_time_total_s": summary.drinking_time_total_s,
                "drinking_bouts": summary.drinking_bouts,
                "drinking_frequency_per_bird": summary.drinking_frequency_per_bird,
                "inactivity_time_total_s": summary.inactivity_time_total_s,
                "inactivity_pct": summary.inactivity_pct,
                "lethargic_birds_flagged": summary.lethargic_birds_flagged,
                "feather_pecking_events": summary.feather_pecking_events,
                "agitation_score": summary.agitation_score,
            }
            chunk_metrics_results.append(chunk_record)
            chunk_bird_records.append(summary.bird_records)
            chunk_summaries.append(summary)

            if video_ended:
                end_dt = actual_chunk_end
                break

        if not chunk_metrics_results:
            raise RuntimeError(f"No frames could be analyzed in {filename}")

        # ── Save analysis report for the annotated output video ───────────────
        stem = Path(filename).stem
        path_suffix = hashlib.sha256(str(Path(video_path).resolve()).encode("utf-8")).hexdigest()[:10]
        results_path = write_results_json(f"{stem}_{path_suffix}", {
            "job_id": job.id,
            "source_video": filename,
            "source_path": video_path,
            "detected_video": str(detected_path) if detected_path else None,
            "processed_at": datetime.datetime.now().isoformat(),
            "window": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
            "chunks": chunk_metrics_results,
            "bird_records": chunk_bird_records,
        })
        print(f"[LOCAL STORE] Saved detection results JSON: {results_path}")

        # Persist results to SQLite/SQLAlchemy database
        DB.save_30m_metrics(job.id, chunk_metrics_results, chunk_bird_records)
        elapsed_s = time.time() - t_start_processing

        print(f"\n[WORKER SUCCESS] Successfully processed hourly block: {filename}")
        print(f"                Elapsed Time: {elapsed_s:.1f}s | Inferred Frames: {frames_processed}")
        print(f"                Analysis intervals: {len(chunk_summaries)}")
        for index, summary in enumerate(chunk_summaries, start=1):
            print(
                f"                Interval {index}: {summary.unique_birds} birds | "
                f"Inactivity: {summary.inactivity_pct:.1f}% | "
                f"Pecking: {summary.feather_pecking_events}"
            )
        return frames_processed, elapsed_s

    def _open_detected_writer(
        self,
        filename: str,
        width: int,
        height: int,
        fps: float,
        stride: int,
    ) -> Tuple[Optional[cv2.VideoWriter], Optional[str], Optional[str]]:
        """Writes to a staging file so sync only sees a completed annotated video."""
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        stem = Path(filename).stem
        out_path = str(VIDEOS_DIR / f"{stem}_detected.mp4")
        staging_path = str(VIDEOS_DIR / f"{stem}_detected.processing.mp4")
        if os.path.isfile(staging_path):
            os.remove(staging_path)
        write_fps = max(5.0, (fps or 20.0) / max(1, stride))
        size = (max(2, width), max(2, height))
        for codec in ("mp4v", "XVID", "avc1"):
            writer = cv2.VideoWriter(
                staging_path, cv2.VideoWriter_fourcc(*codec), write_fps, size
            )
            if writer.isOpened():
                print(f"[RECORDER] Writing annotated video: {staging_path} ({size[0]}x{size[1]} @ {write_fps:.1f}fps, {codec})")
                return writer, out_path, staging_path
            writer.release()
            if os.path.isfile(staging_path):
                os.remove(staging_path)
        print("[RECORDER WARN] Could not open VideoWriter for annotated output.")
        return None, None, None


def main():
    worker = ZampenAnalyticsWorker()
    worker.start_daemon()


if __name__ == "__main__":
    main()
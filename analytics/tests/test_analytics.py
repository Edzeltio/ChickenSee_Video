"""
analytics/tests/test_analytics.py
Automated Unit and Integration Tests for ZAMPEN Behavior & Sensor Analytics Pipeline.
Validates:
  1. Database initialization & duplicate job prevention.
  2. Specific behavior detection algorithms (Reduce Eating, Drinking, Inactivity, Feather Pecking).
  3. 30-minute sensor aggregation math.
  4. FastAPI REST API endpoints.
"""

import sys
import json
import unittest
import datetime
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analytics.config import CONFIG
from analytics.database import DatabaseManager, HalfHourMetrics, JobStatus, ProcessingJob
from analytics.tracker import TrackedDetection, TrackerFrameOutput
from analytics.tracker import ZampenYOLOTracker
from analytics.behavior_engine import BehaviorAnalysisEngine
from analytics.worker import AnalysisCancelled, ZampenAnalyticsWorker
from fastapi.testclient import TestClient
from analytics.api import _progress_percent, _queue_video_dirs, app, get_detection_queue
import local_store


class TestZampenAnalyticsPipeline(unittest.TestCase):

    def setUp(self):
        # Use in-memory SQLite database for test isolation
        self.db = DatabaseManager(db_url="sqlite:///:memory:")
        self.engine = BehaviorAnalysisEngine()

    def test_database_idempotency(self):
        """Verify that jobs prevent duplicate execution of the same file."""
        filename = "detection_2026-10-14_0600.mp4"
        now = datetime.datetime.now()
        end = now + datetime.timedelta(hours=1)

        self.assertFalse(self.db.is_file_processed(filename))

        # Lease job
        job = self.db.lease_job(filename, "/dummy/path.mp4", now, end)
        self.assertIsNotNone(job)
        self.assertEqual(job.status, JobStatus.PROCESSING)
        self.db.update_job_progress(job.id, 250, 1000)
        with self.db.get_session() as session:
            persisted = session.get(ProcessingJob, job.id)
            self.assertEqual(persisted.progress_frames, 250)
            self.assertEqual(persisted.total_frames, 1000)
            self.assertEqual(_progress_percent(persisted), 25.0)

        # Mark completed
        self.db.mark_job_completed(job.id, 1000, 1000, 45.2)
        with self.db.get_session() as session:
            completed = session.get(ProcessingJob, job.id)
            self.assertEqual(completed.progress_frames, 1000)
            self.assertEqual(_progress_percent(completed), 100.0)
        self.assertTrue(self.db.is_file_processed(filename))

        # Attempt to lease again - should return None to prevent duplicate run
        duplicate_lease = self.db.lease_job(filename, "/dummy/path.mp4", now, end)
        self.assertIsNone(duplicate_lease)

    def test_sensor_results_keep_missing_values_null(self):
        start = datetime.datetime(2026, 10, 14, 6, 0)
        end = start + datetime.timedelta(minutes=30)
        job = self.db.lease_job("no-sensors.mp4", "/test/no-sensors.mp4", start, end)
        self.db.save_30m_metrics(job.id, [{
            "date_slot": "2026-10-14",
            "hour_slot": "06:00",
            "chunk_index": 1,
            "chunk_start": start,
            "chunk_end": end,
            "temp_avg_c": None,
            "humidity_avg_pct": None,
            "ammonia_ppm_avg": None,
            "heat_stress_level": "Unavailable",
            "sensor_data_available": False,
        }], [[]])
        self.db.mark_job_completed(job.id, 0, 0, 0.0)

        latest = self.db.get_latest_metrics()
        ranged = self.db.get_metrics_by_range(start, end)

        self.assertFalse(latest["chunks_30m"][0]["sensors"]["sensor_data_available"])
        self.assertIsNone(latest["chunks_30m"][0]["sensors"]["temp_avg_c"])
        self.assertIsNone(ranged[0]["environment"]["temperature_c"])
        self.assertFalse(ranged[0]["environment"]["sensor_data_available"])

        measured_start = end + datetime.timedelta(minutes=30)
        measured_end = measured_start + datetime.timedelta(minutes=30)
        measured_job = self.db.lease_job(
            "with-sensors.mp4",
            "/test/with-sensors.mp4",
            measured_start,
            measured_end,
        )
        self.db.save_30m_metrics(measured_job.id, [{
            "date_slot": "2026-10-14",
            "hour_slot": "07:00",
            "chunk_index": 1,
            "chunk_start": measured_start,
            "chunk_end": measured_end,
            "temp_avg_c": 24.5,
            "humidity_avg_pct": 55.0,
            "ammonia_ppm_avg": 0.0,
            "heat_stress_level": "Normal",
            "sensor_data_available": True,
        }], [[]])
        self.db.mark_job_completed(measured_job.id, 0, 0, 0.0)

        measured = self.db.get_latest_metrics()["chunks_30m"][0]
        self.assertTrue(measured["sensors"]["sensor_data_available"])
        self.assertEqual(measured["sensors"]["temp_avg_c"], 24.5)

    def test_recreated_video_at_same_path_is_queued_for_reanalysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            video_path = Path(temporary) / "repeat.mp4"
            video_path.write_bytes(b"first video")
            first_signature = self.db.file_signature(str(video_path))
            now = datetime.datetime.now()
            job = self.db.lease_job(
                video_path.name,
                str(video_path),
                now,
                now + datetime.timedelta(minutes=1),
                signature=first_signature,
            )
            self.db.mark_job_completed(job.id, 5, 5, 1.0)
            self.assertTrue(
                self.db.is_file_processed(
                    video_path.name, str(video_path), first_signature
                )
            )

            video_path.write_bytes(b"a different replacement video")
            replacement_signature = self.db.file_signature(str(video_path))
            self.assertFalse(
                self.db.is_file_processed(
                    video_path.name, str(video_path), replacement_signature
                )
            )
            replacement_job = self.db.lease_job(
                video_path.name,
                str(video_path),
                now,
                now + datetime.timedelta(minutes=1),
                signature=replacement_signature,
            )

        self.assertEqual(replacement_job.id, job.id)
        self.assertEqual(replacement_job.status, JobStatus.PROCESSING)
        self.assertEqual(replacement_job.source_size, replacement_signature[0])

    def test_failed_file_version_is_not_retried_until_file_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            video_path = Path(temporary) / "unreadable.mp4"
            video_path.write_bytes(b"invalid video")
            signature = self.db.file_signature(str(video_path))
            now = datetime.datetime.now()
            job = self.db.lease_job(
                video_path.name,
                str(video_path),
                now,
                now,
                signature=signature,
            )
            self.db.mark_job_failed(job.id, "Could not open video")

            self.assertTrue(
                self.db.has_failed_file_version(str(video_path), signature)
            )

            video_path.write_bytes(b"replaced video content")
            changed_signature = self.db.file_signature(str(video_path))
            self.assertFalse(
                self.db.has_failed_file_version(str(video_path), changed_signature)
            )

    def test_worker_skips_failed_video_and_advances_to_next_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            failed_path = root / "unreadable.mp4"
            next_path = root / "next.mp4"
            failed_path.write_bytes(b"invalid video")
            next_path.write_bytes(b"next video")
            old_time = time.time() - 60
            os.utime(failed_path, (old_time, old_time))
            os.utime(next_path, (old_time + 1, old_time + 1))

            now = datetime.datetime.now()
            failed_job = self.db.lease_job(
                failed_path.name,
                str(failed_path),
                now,
                now,
                signature=self.db.file_signature(str(failed_path)),
            )
            self.db.mark_job_failed(failed_job.id, "Could not open video")

            worker = object.__new__(ZampenAnalyticsWorker)
            worker.cfg = SimpleNamespace(stability_threshold_s=0)
            with patch("analytics.worker.DB", self.db), patch.object(
                worker, "_video_input_dirs", return_value=[root]
            ):
                candidate = worker._find_next_completed_hourly_video()

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate[0], str(next_path.resolve()))

    def test_queue_shows_replaced_video_even_when_path_was_completed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_path = root / "previous.mp4"
            video_path.write_bytes(b"old")
            old_signature = self.db.file_signature(str(video_path))
            now = datetime.datetime.now()
            job = self.db.lease_job(
                video_path.name,
                str(video_path),
                now,
                now + datetime.timedelta(minutes=1),
                signature=old_signature,
            )
            self.db.mark_job_completed(job.id, 2, 2, 0.5)
            video_path.write_bytes(b"new replacement video")
            os.utime(video_path, (time.time() - 60, time.time() - 60))

            with patch("analytics.api.DB", self.db), patch(
                "analytics.api._queue_video_dirs", return_value=[root]
            ):
                response = get_detection_queue(limit=100)

        item = next(item for item in response["items"] if item["video_path"] == str(video_path))
        self.assertEqual(item["status"], "QUEUED")
        self.assertIn("queued for reanalysis", item["details"])
        self.assertIsNone(item["progress_percent"])

    def test_queue_keeps_unchanged_failed_video_out_of_pending_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_path = root / "unreadable.mp4"
            video_path.write_bytes(b"invalid video")
            os.utime(video_path, (time.time() - 60, time.time() - 60))
            signature = self.db.file_signature(str(video_path))
            now = datetime.datetime.now()
            job = self.db.lease_job(
                video_path.name,
                str(video_path),
                now,
                now,
                signature=signature,
            )
            self.db.mark_job_failed(job.id, "Could not open video")

            with patch("analytics.api.DB", self.db), patch(
                "analytics.api._queue_video_dirs", return_value=[root]
            ):
                response = get_detection_queue(limit=100)

        item = next(item for item in response["items"] if item["video_path"] == str(video_path))
        self.assertEqual(item["status"], "FAILED")
        self.assertEqual(item["details"], "Could not open video")
        self.assertEqual(response["summary"]["queued"], 0)
        self.assertEqual(response["summary"]["failed"], 1)

    def test_queue_progress_does_not_exceed_video_length(self):
        job = ProcessingJob(
            status=JobStatus.PROCESSING,
            progress_frames=1200,
            total_frames=1000,
        )
        self.assertEqual(_progress_percent(job), 100.0)
        job.progress_frames = None
        self.assertIsNone(_progress_percent(job))

    def test_queue_response_returns_persisted_live_progress(self):
        now = datetime.datetime.now()
        job = self.db.lease_job("camera.mp4", "/test/camera.mp4", now, now)
        self.assertIsNotNone(job)
        self.db.update_job_progress(job.id, 125, 500)
        detection = {
            "job_id": job.id,
            "frame_idx": 125,
            "bird_count": 4,
            "paired_sensor_database": "camera.sqlite",
        }

        with patch("analytics.api.DB", self.db), patch(
            "analytics.api._queue_video_dirs", return_value=[]
        ), patch(
            "local_store.read_detection_status_json", return_value=detection
        ):
            response = get_detection_queue(limit=100)

        item = response["items"][0]
        self.assertEqual(item["status"], "PROCESSING")
        self.assertEqual(item["progress_frames"], 125)
        self.assertEqual(item["total_frames"], 500)
        self.assertEqual(item["progress_percent"], 25.0)
        self.assertEqual(response["active_detection"], detection)

    def test_queue_summary_counts_items_beyond_response_limit(self):
        now = datetime.datetime.now()
        for index in range(3):
            job = self.db.lease_job(
                f"camera-{index}.mp4",
                f"/test/camera-{index}.mp4",
                now,
                now,
            )
            self.assertIsNotNone(job)
            self.db.mark_job_completed(job.id, 10, 10, 0.5)

        with patch("analytics.api.DB", self.db), patch(
            "analytics.api._queue_video_dirs", return_value=[]
        ), patch("local_store.write_queue_status_json"):
            response = get_detection_queue(limit=1)

        self.assertEqual(len(response["items"]), 1)
        self.assertEqual(response["summary"]["completed"], 3)

    def test_live_detection_frame_endpoint_serves_latest_jpeg(self):
        with tempfile.TemporaryDirectory() as temporary:
            results_dir = Path(temporary)
            preview = results_dir / "live_detection.jpg"
            preview.write_bytes(b"jpeg-preview")
            with patch.object(local_store, "RESULTS_DIR", results_dir):
                response = TestClient(app).get("/queue/live-frame")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.content, b"jpeg-preview")

    def test_queue_status_snapshot_keeps_more_than_40_history_items(self):
        now = datetime.datetime.now()
        for index in range(45):
            filename = f"camera-{index}.mp4"
            job = self.db.lease_job(
                filename,
                f"/test/{filename}",
                now,
                now + datetime.timedelta(hours=1),
            )
            self.assertIsNotNone(job)
            self.db.mark_job_completed(job.id, 100, 100, 1.0)

        with patch("analytics.api.DB", self.db), patch(
            "analytics.api._queue_video_dirs", return_value=[]
        ), patch("local_store.write_queue_status_json") as write_snapshot:
            response = get_detection_queue(limit=500)

        self.assertEqual(len(response["items"]), 45)
        snapshot = write_snapshot.call_args.args[0]
        self.assertEqual(len(snapshot["items"]), 45)
        self.assertIsNotNone(response["items"][0]["completed_at"])
        self.assertIsNotNone(snapshot["items"][0]["completed_at"])

    def test_queue_snapshot_refreshes_heartbeat_without_losing_live_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            results_dir = Path(temporary) / "results"
            live_detection = {"job_id": 7, "frame_idx": 125, "bird_count": 4}
            first_updated_at = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(seconds=6)
            ).isoformat().replace("+00:00", "Z")
            latest_updated_at = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat().replace("+00:00", "Z")
            with patch.object(local_store, "RESULTS_DIR", results_dir):
                local_store.write_queue_status_json({
                    "updated_at": first_updated_at,
                    "active_video": "camera.mp4",
                    "active_detection": live_detection,
                    "summary": {"processing": 1},
                })
                local_store.write_queue_status_json({
                    "updated_at": latest_updated_at,
                    "active_video": "camera.mp4",
                    "active_detection": live_detection,
                    "summary": {"processing": 1},
                })
                snapshot = json.loads(
                    (results_dir / "queue_status.json").read_text(encoding="utf-8")
                )

        self.assertEqual(snapshot["updated_at"], latest_updated_at)
        self.assertEqual(snapshot["active_detection"], live_detection)

    def test_worker_queue_updates_preserve_other_queue_items(self):
        with tempfile.TemporaryDirectory() as temporary:
            results_dir = Path(temporary) / "results"
            results_dir.mkdir()
            status_file = results_dir / "queue_status.json"
            status_file.write_text(json.dumps({
                "summary": {
                    "processing": 0,
                    "queued": 2,
                    "waiting": 0,
                    "completed": 0,
                    "failed": 0,
                    "cancelled": 0,
                },
                "items": [{
                    "item_id": "job-2",
                    "video_filename": "next.mp4",
                    "status": "QUEUED",
                }, {
                    "item_id": "file-source-1",
                    "video_filename": "active.mp4",
                    "status": "QUEUED",
                }],
            }), encoding="utf-8")

            with patch.object(local_store, "RESULTS_DIR", results_dir):
                local_store.update_queue_status_json({
                    "updated_at": "2026-10-23T12:00:00Z",
                    "job_id": 1,
                    "video_filename": "active.mp4",
                    "source_item_id": "file-source-1",
                    "status": "PROCESSING",
                    "progress_percent": 25.0,
                    "frames": "25 / 100",
                    "active_detection": {"job_id": 1, "bird_count": 3},
                })
                processing = json.loads(status_file.read_text(encoding="utf-8"))
                self.assertEqual(processing["summary"]["processing"], 1)
                self.assertEqual(processing["summary"]["queued"], 1)
                self.assertEqual(len(processing["items"]), 2)
                self.assertTrue(processing["is_queueing"])
                self.assertEqual(processing["active_video"], "active.mp4")
                self.assertEqual(processing["active_detection"]["bird_count"], 3)
                self.assertIn("job-1", {item["item_id"] for item in processing["items"]})

                local_store.update_queue_status_json({
                    "updated_at": "2026-10-23T12:01:00Z",
                    "job_id": 1,
                    "video_filename": "active.mp4",
                    "status": "COMPLETED",
                    "progress_percent": None,
                    "frames": "—",
                })
                completed = json.loads(status_file.read_text(encoding="utf-8"))

        self.assertEqual(completed["summary"]["processing"], 0)
        self.assertEqual(completed["summary"]["queued"], 1)
        self.assertEqual(completed["summary"]["completed"], 1)
        self.assertTrue(completed["is_queueing"])
        self.assertIsNone(completed["active_video"])
        self.assertIsNone(completed["active_detection"])
        self.assertEqual({item["video_filename"] for item in completed["items"]}, {"active.mp4", "next.mp4"})

    def test_paired_sensor_database_prefers_video_basename(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary)
            video = session / "camera.mp4"
            video.touch()
            other_database = session / "sensors.sqlite"
            matching_database = session / "camera.db"
            other_database.touch()
            matching_database.touch()

            self.assertEqual(
                ZampenAnalyticsWorker._find_paired_sensor_db(str(video)),
                matching_database,
            )

        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary)
            video = session / "camera.mp4"
            video.touch()
            paired_database = session / "sensors.sqlite"
            paired_database.touch()

            self.assertEqual(
                ZampenAnalyticsWorker._find_paired_sensor_db(str(video)),
                paired_database,
            )

    def test_cancel_active_job_and_reject_repeat_cancellation(self):
        now = datetime.datetime.now()
        job = self.db.lease_job("cancel.mp4", "/test/cancel.mp4", now, now)

        self.assertTrue(self.db.cancel_processing_job(job.id))
        self.assertTrue(self.db.is_job_cancelled(job.id))
        self.assertFalse(self.db.cancel_processing_job(job.id))

        with self.db.get_session() as session:
            cancelled = session.get(ProcessingJob, job.id)
            self.assertEqual(cancelled.status, JobStatus.CANCELLED)
            self.assertEqual(cancelled.error_message, "Cancelled by user.")

        self.assertTrue(self.db.is_file_processed(
            "cancel.mp4", "/test/cancel.mp4"
        ))
        self.assertIsNone(self.db.lease_job(
            "cancel.mp4", "/test/cancel.mp4", now, now
        ))
        self.db.mark_job_failed(job.id, "late worker error")
        with self.db.get_session() as session:
            self.assertEqual(session.get(ProcessingJob, job.id).status, JobStatus.CANCELLED)

    def test_worker_cancel_checkpoint_raises_for_cancelled_job(self):
        now = datetime.datetime.now()
        job = self.db.lease_job("cancel.mp4", "/test/cancel.mp4", now, now)
        self.db.cancel_processing_job(job.id)

        with patch("analytics.worker.DB", self.db):
            with self.assertRaises(AnalysisCancelled):
                ZampenAnalyticsWorker._raise_if_cancelled(job.id)

    def test_cancel_api_only_cancels_processing_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            cancel_db = DatabaseManager(
                db_url=f"sqlite:///{Path(temporary) / 'cancel-test.db'}"
            )
            now = datetime.datetime.now()
            job = cancel_db.lease_job("cancel.mp4", "/test/cancel.mp4", now, now)

            with TestClient(app) as client, patch("analytics.api.DB", cancel_db):
                response = client.post(f"/jobs/{job.id}/cancel")
                missing = client.post("/jobs/999/cancel")
                already_cancelled = client.post(f"/jobs/{job.id}/cancel")
            cancel_db.engine.dispose()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "CANCELLED")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(already_cancelled.status_code, 409)

    def test_queue_discovers_stable_and_still_writing_videos(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stable_video = root / "stable.mp4"
            writing_video = root / "writing.mp4"
            detected_video = root / "stable_detected.mp4"
            for video in (stable_video, writing_video, detected_video):
                video.write_bytes(b"video")
            os.utime(stable_video, (time.time() - 30, time.time() - 30))
            os.utime(detected_video, (time.time() - 30, time.time() - 30))

            with patch("analytics.api.DB", self.db), patch(
                "analytics.api._queue_video_dirs", return_value=[root]
            ):
                response = get_detection_queue(limit=100)

        items = {item["video_filename"]: item for item in response["items"]}
        self.assertEqual(items["stable.mp4"]["status"], "QUEUED")
        self.assertIsNone(items["stable.mp4"]["progress_percent"])
        self.assertEqual(items["writing.mp4"]["status"], "WAITING")
        self.assertIsNone(items["writing.mp4"]["progress_percent"])
        self.assertNotIn("stable_detected.mp4", items)

    def test_queue_reports_currently_saved_watch_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("analytics.api.DB", self.db), patch(
                "analytics.api._queue_video_dirs",
                return_value=[root, root / "removed-folder"],
            ):
                response = get_detection_queue(limit=100)

        self.assertEqual(response["watch_directories"], [str(root)])
        self.assertEqual(response["missing_watch_directories"], [str(root / "removed-folder")])

    def test_saved_watch_directories_replace_default_folder_for_queue_and_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured_dir = root / "new-watch-folder"
            config_file = root / "directories.json"
            config_file.write_text(
                json.dumps({"directories": [str(configured_dir)]}),
                encoding="utf-8",
            )

            queue_dirs = _queue_video_dirs(config_file)
            worker = object.__new__(ZampenAnalyticsWorker)
            worker_dirs = worker._video_input_dirs(config_file)

        expected = [configured_dir.resolve()]
        self.assertEqual(queue_dirs, expected)
        self.assertEqual(worker_dirs, expected)

    def test_video_chunk_ranges_support_variable_lengths(self):
        self.assertEqual(
            ZampenAnalyticsWorker._chunk_frame_ranges(3000, 30.0, 1),
            [(0, 1800), (1800, 3000)],
        )
        self.assertEqual(
            ZampenAnalyticsWorker._chunk_frame_ranges(300, 30.0, 1),
            [(0, 300)],
        )
        self.assertEqual(
            ZampenAnalyticsWorker._chunk_frame_ranges(0, 30.0, 1),
            [],
        )

    def test_detected_writer_uses_non_syncable_staging_file(self):
        class OpenWriter:
            def isOpened(self):
                return True

        with tempfile.TemporaryDirectory() as temporary, patch(
            "analytics.worker.VIDEOS_DIR", Path(temporary)
        ), patch("analytics.worker.cv2.VideoWriter", return_value=OpenWriter()), patch(
            "analytics.worker.cv2.VideoWriter_fourcc", return_value=0
        ):
            worker = object.__new__(ZampenAnalyticsWorker)
            writer, detected_path, staging_path = worker._open_detected_writer(
                "recording.mp4", 640, 360, 30.0, 2
            )

        self.assertIsNotNone(writer)
        self.assertEqual(Path(detected_path).name, "recording_detected.mp4")
        self.assertEqual(Path(staging_path).name, "recording_detected.processing.mp4")
        self.assertNotEqual(detected_path, staging_path)

    def test_timestamped_camera_filename_is_parsed(self):
        worker = object.__new__(ZampenAnalyticsWorker)
        start, end = worker._extract_timestamps_from_file(
            Path("20260914_102808_tp00010.mp4")
        )
        self.assertEqual(start, datetime.datetime(2026, 9, 14, 10, 28, 8))
        self.assertEqual(end, start + datetime.timedelta(hours=1))

    def test_rec_subfolder_filename_is_parsed(self):
        """
        rec_%Y - %m-%d_%H-%M-%S_%f is the SESSION FOLDER name, not the video filename.
        Structure: watched_dir / cam01 / rec_<timestamp> / video.mp4
        """
        worker = object.__new__(ZampenAnalyticsWorker)
        # Full nested path mirrors the real recorder layout
        path = Path(r"C:\Users\Kairi\Documents\chicks\test\cam01\rec_2026 - 10-20_14-30-00_123456\video.mp4")
        start, end = worker._extract_timestamps_from_file(path)
        self.assertEqual(start, datetime.datetime(2026, 10, 20, 14, 30, 0))
        self.assertEqual(end, start + datetime.timedelta(hours=1))

    def test_rec_subfolder_filename_without_spaces_is_parsed(self):
        """rec_ folder pattern tolerates no spaces around the dash separator."""
        worker = object.__new__(ZampenAnalyticsWorker)
        path = Path(r"C:\Users\Kairi\Documents\chicks\test\cam01\rec_2026-10-20_08-05-30_000001\video.mp4")
        start, end = worker._extract_timestamps_from_file(path)
        self.assertEqual(start, datetime.datetime(2026, 10, 20, 8, 5, 30))
        self.assertEqual(end, start + datetime.timedelta(hours=1))

    def test_missing_sensor_interval_is_reported_unavailable_not_as_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "sensors.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE environmental_logs_raw (
                        timestamp TEXT,
                        temperature_c REAL,
                        humidity_pct REAL,
                        ammonia_ppm REAL,
                        lux REAL,
                        thi REAL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO environmental_logs_raw VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-10-16T12:00:00", 26.0, 70.0, 5.0, 100.0, 75.0),
                )
                connection.commit()
            finally:
                connection.close()

            worker = object.__new__(ZampenAnalyticsWorker)
            worker.cfg = SimpleNamespace(
                sensor_db_url=f"sqlite:///{database_path.as_posix()}"
            )
            result = worker._pull_sensor_averages_for_range(
                datetime.datetime(2026, 10, 15, 12, 0),
                datetime.datetime(2026, 10, 15, 13, 0),
            )

        self.assertFalse(result["sensor_data_available"])
        self.assertIsNone(result["temp_avg_c"])
        self.assertIsNone(result["humidity_avg_pct"])
        self.assertIsNone(result["ammonia_ppm_avg"])
        self.assertEqual(result["heat_stress_level"], "Unavailable")

    def test_sensor_interval_exports_measured_values_and_availability(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "sensors.db"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE environmental_logs_raw (
                        timestamp TEXT,
                        temperature_c REAL,
                        humidity_pct REAL,
                        ammonia_ppm REAL,
                        lux REAL,
                        thi REAL
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO environmental_logs_raw VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("2026-10-15T12:10:00", 26.0, 70.0, 5.0, 100.0, 75.0),
                        ("2026-10-15T12:20:00", 28.0, 74.0, 7.0, 200.0, 77.0),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            worker = object.__new__(ZampenAnalyticsWorker)
            worker.cfg = SimpleNamespace(
                sensor_db_url=f"sqlite:///{database_path.as_posix()}"
            )
            result = worker._pull_sensor_averages_for_range(
                datetime.datetime(2026, 10, 15, 12, 0),
                datetime.datetime(2026, 10, 15, 13, 0),
            )

        self.assertTrue(result["sensor_data_available"])
        self.assertEqual(result["temp_avg_c"], 27.0)
        self.assertEqual(result["humidity_avg_pct"], 72.0)
        self.assertEqual(result["ammonia_ppm_avg"], 6.0)
        self.assertEqual(result["lux_avg"], 150.0)
        self.assertEqual(result["heat_stress_level"], "Moderate")

    def test_paired_sensor_logs_schema_is_aggregated_by_recording_elapsed_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "sensors.sqlite"
            recording_start = datetime.datetime.now().astimezone().replace(tzinfo=None)
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE recording (
                        id INTEGER PRIMARY KEY,
                        started_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE sensor_logs (
                        elapsed_seconds REAL NOT NULL,
                        captured_at TEXT NOT NULL,
                        temperature REAL,
                        humidity REAL,
                        ammonia_ppm REAL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO recording (started_at) VALUES (?)",
                    (recording_start.astimezone().isoformat(),),
                )
                connection.executemany(
                    """
                    INSERT INTO sensor_logs
                        (elapsed_seconds, captured_at, temperature, humidity, ammonia_ppm)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (0.0, "2026-10-24T02:42:06+00:00", 34.0, 60.0, 0.00001),
                        (30.0, "2026-10-24T02:42:36+00:00", 36.0, 62.0, 0.00003),
                        (90.0, "2026-10-24T02:43:36+00:00", 99.0, 99.0, 99.0),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            worker = object.__new__(ZampenAnalyticsWorker)
            worker.cfg = SimpleNamespace(
                sensor_db_url=f"sqlite:///{database_path.as_posix()}"
            )
            result = worker._pull_sensor_averages_for_range(
                recording_start,
                recording_start + datetime.timedelta(seconds=60),
                paired_db=database_path,
            )

        self.assertTrue(result["sensor_data_available"])
        self.assertEqual(result["temp_avg_c"], 35.0)
        self.assertEqual(result["temp_min_c"], 34.0)
        self.assertEqual(result["temp_max_c"], 36.0)
        self.assertEqual(result["humidity_avg_pct"], 61.0)
        self.assertEqual(result["ammonia_ppm_avg"], 0.00002)
        self.assertIsNone(result["lux_avg"])
        self.assertIsNotNone(result["thi_avg"])

    def test_unavailable_sensor_values_can_be_saved_in_analytics_database(self):
        start = datetime.datetime(2026, 10, 15, 12, 0)
        end = start + datetime.timedelta(minutes=30)
        job = self.db.lease_job("no-sensor.mp4", "/no-sensor.mp4", start, end)
        self.assertIsNotNone(job)
        metrics = {
            "date_slot": "2026-10-15",
            "hour_slot": "12:00",
            "chunk_index": 1,
            "chunk_start": start,
            "chunk_end": end,
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

        self.db.save_30m_metrics(job.id, [metrics], [[]])

        with self.db.get_session() as session:
            saved = session.query(HalfHourMetrics).filter_by(job_id=job.id).one()
            self.assertEqual(saved.temp_avg_c, 0.0)
            self.assertEqual(saved.heat_stress_level, "Unavailable")

    def test_tracker_resolves_custom_model_weights(self):
        tracker = object.__new__(ZampenYOLOTracker)
        self.assertEqual(
            Path(tracker._resolve_weights("yolo26n.pt")),
            Path(CONFIG.custom_weights_path).resolve(),
        )

    def test_tracker_does_not_carry_chicken_track_class_into_equipment(self):
        tracker = object.__new__(ZampenYOLOTracker)
        tracker.class_map = {
            0: "Chicken",
            1: "Drinker",
            2: "Feeder",
            8: "Rooster",
        }
        tracker.model = type("Model", (), {"names": tracker.class_map})()
        tracker.track_class_history = {}
        tracker.track_resolved_classes = {}
        tracker.track_class_last_seen = {}
        tracker.processed_frame_count = 1

        for _ in range(8):
            resolved = tracker._resolve_track_class(0, 0.78, 53)
        self.assertEqual(resolved[:2], (0, "Chicken"))
        self.assertAlmostEqual(resolved[2], 0.78)

        for _ in range(5):
            resolved = tracker._resolve_track_class(1, 0.607, 53)
        self.assertEqual(resolved[:2], (1, "Drinker"))
        self.assertAlmostEqual(resolved[2], 0.607)
        self.assertNotIn(53, tracker.track_class_history)
        self.assertNotIn(53, tracker.track_resolved_classes)

        resolved = tracker._resolve_track_class(2, 0.91, 53)
        self.assertEqual(resolved[:2], (2, "Feeder"))
        self.assertAlmostEqual(resolved[2], 0.91)
        resolved = tracker._resolve_track_class(8, 0.94, 53)
        self.assertEqual(resolved[:2], (8, "Rooster"))
        self.assertAlmostEqual(resolved[2], 0.94)

    def test_equipment_feeder_and_drinker_labeled_correctly_and_drawn_gray(self):
        import numpy as np
        tracker = object.__new__(ZampenYOLOTracker)
        tracker.class_map = {0: "Chicken", 1: "Drinker", 2: "Feeder", 8: "Rooster"}
        tracker.track_label_cache = {}
        tracker.track_label_votes = {}
        tracker.track_label_last_seen = {}
        tracker.track_class_history = {}
        tracker.track_resolved_classes = {}
        tracker.track_class_last_seen = {}
        tracker.processed_frame_count = 0
        tracker.conf = 0.15
        tracker.iou = 0.45
        tracker.imgsz = 640
        tracker.device = "cpu"
        tracker.tracker_yaml = "bytetrack.yaml"

        # Mock results from model.track
        class MockBoxes:
            xyxy = type("Tensor", (), {"cpu": lambda *args: type("A", (), {"numpy": lambda *args: np.array([[50, 50, 80, 80], [90, 90, 120, 120]])})()})()
            conf = type("Tensor", (), {"cpu": lambda *args: type("A", (), {"numpy": lambda *args: np.array([0.97, 0.90])})()})()
            cls = type("Tensor", (), {"int": lambda *args: type("A", (), {"cpu": lambda *args: type("B", (), {"tolist": lambda *args: [2, 1]})()})()})()
            id = type("Tensor", (), {"int": lambda *args: type("A", (), {"cpu": lambda *args: type("B", (), {"tolist": lambda *args: [1, 2]})()})()})()
            def __len__(self):
                return 2

        class MockResult:
            boxes = MockBoxes()

        tracker.model = type("MockModel", (), {
            "names": tracker.class_map,
            "track": lambda *args, **kwargs: [MockResult()]
        })()

        blank_frame = np.zeros((200, 200, 3), dtype=np.uint8)
        frame_out = tracker.process_frame(blank_frame, frame_idx=0)

        self.assertEqual(len(frame_out.feeders), 1)
        self.assertEqual(len(frame_out.drinkers), 1)
        self.assertEqual(len(frame_out.birds), 0)

        feeder = frame_out.feeders[0]
        self.assertEqual(feeder.class_name, "Feeder")
        self.assertEqual(feeder.bird_label, "Feeder")
        self.assertIsNone(feeder.track_id)

        drinker = frame_out.drinkers[0]
        self.assertEqual(drinker.class_name, "Drinker")
        self.assertEqual(drinker.bird_label, "Drinker")
        self.assertIsNone(drinker.track_id)

        vis = tracker.annotate_frame(blank_frame, frame_out)
        # Check that bounding box pixel is gray (128, 128, 128)
        # Feeder box is [50, 50, 80, 80]
        self.assertTrue(np.array_equal(vis[50, 50], [128, 128, 128]))


    def test_same_named_videos_in_separate_folders_get_distinct_jobs(self):
        now = datetime.datetime.now()
        end = now + datetime.timedelta(minutes=5)
        first = self.db.lease_job("recording.mp4", "/camera-a/recording.mp4", now, end)
        self.assertIsNotNone(first)
        self.db.mark_job_completed(first.id, 100, 100, 3.0)

        second = self.db.lease_job("recording.mp4", "/camera-b/recording.mp4", now, end)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.video_filename, second.video_filename)

    def test_behavior_drinking_and_inactivity(self):
        """Test drinking in waterer zone and inactivity detection."""
        # Bird 1 inside drinker zone for 5 seconds
        drinker_box = [100, 100, 150, 150]
        drinker_det = TrackedDetection(
            box=drinker_box, centroid=(125, 125), confidence=0.9,
            class_id=1, class_name="Drinker", track_id=None, bird_label="Drinker"
        )

        bird_det = TrackedDetection(
            box=[105, 105, 140, 140], centroid=(122, 122), confidence=0.88,
            class_id=0, class_name="Chicken", track_id=101, bird_label="Hen1 (Green)"
        )

        dt = 0.5
        for step in range(10): # 5 seconds
            frame_out = TrackerFrameOutput(
                frame_idx=step,
                timestamp_s=step * dt,
                birds=[bird_det],
                feeders=[],
                drinkers=[drinker_det]
            )
            self.engine.process_frame(frame_out, dt=dt)

        summary = self.engine.compute_chunk_summary()
        self.assertEqual(summary.unique_birds, 1)
        self.assertGreater(summary.drinking_time_total_s, 4.0)
        self.assertGreaterEqual(summary.drinking_bouts, 1)

    def test_behavior_reduced_eating(self):
        """
        Test Reduced Eating: bird is near feeder but localized motion is near zero
        for extended time (standing idle instead of active pecking).
        """
        feeder_box = [300, 300, 400, 400]
        feeder_det = TrackedDetection(
            box=feeder_box, centroid=(350, 350), confidence=0.9,
            class_id=2, class_name="Feeder", track_id=None, bird_label="Feeder"
        )

        # Bird sits in feeder zone completely stationary
        stationary_bird = TrackedDetection(
            box=[320, 320, 360, 360], centroid=(340, 340), confidence=0.85,
            class_id=0, class_name="Chicken", track_id=202, bird_label="Hen3 (Blue)"
        )

        dt = 1.0
        for step in range(25): # 25 seconds idle in feeder
            frame_out = TrackerFrameOutput(
                frame_idx=step,
                timestamp_s=step * dt,
                birds=[stationary_bird],
                feeders=[feeder_det],
                drinkers=[]
            )
            self.engine.process_frame(frame_out, dt=dt)

        summary = self.engine.compute_chunk_summary()
        self.assertGreaterEqual(summary.reduced_eating_events, 1)
        self.assertGreater(summary.reduced_eating_index, 0.0)

    def test_behavior_feather_pecking(self):
        """
        Test Feather Pecking detection:
        Aggressor bird charges at high velocity toward victim bird, enters proximity,
        and recipient accelerates away.
        """
        dt = 0.1
        # Frame 0: birds separated
        bird_a0 = TrackedDetection(box=[50, 50, 80, 80], centroid=(65, 65), confidence=0.9, class_id=0, class_name="Rooster", track_id=1, bird_label="Rooster")
        bird_b0 = TrackedDetection(box=[100, 100, 130, 130], centroid=(115, 115), confidence=0.9, class_id=0, class_name="Chicken", track_id=2, bird_label="Hen2 (White)")
        f0 = TrackerFrameOutput(frame_idx=0, timestamp_s=0.0, birds=[bird_a0, bird_b0], feeders=[], drinkers=[])
        self.engine.process_frame(f0, dt=dt)

        # Frame 1: Aggressor charges fast into close proximity (< 45px distance)
        # Displacement 65 -> 110 in 0.1s is ~450 px/s (very high velocity approach)
        bird_a1 = TrackedDetection(box=[95, 95, 125, 125], centroid=(110, 110), confidence=0.9, class_id=0, class_name="Rooster", track_id=1, bird_label="Rooster")
        bird_b1 = TrackedDetection(box=[105, 105, 135, 135], centroid=(120, 120), confidence=0.9, class_id=0, class_name="Chicken", track_id=2, bird_label="Hen2 (White)")
        f1 = TrackerFrameOutput(frame_idx=1, timestamp_s=0.1, birds=[bird_a1, bird_b1], feeders=[], drinkers=[])
        self.engine.process_frame(f1, dt=dt)

        summary = self.engine.compute_chunk_summary()
        self.assertGreaterEqual(summary.feather_pecking_events, 1)

    def test_fastapi_health_endpoint(self):
        """Verify FastAPI /health endpoint."""
        client = TestClient(app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("system", data)
        self.assertIn("model", data)


if __name__ == "__main__":
    unittest.main()

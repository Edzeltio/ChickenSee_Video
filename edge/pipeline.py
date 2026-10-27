"""
edge/pipeline.py - Production Master Edge AI Pipeline for Dell OptiPlex.
Combines camera stream ingestion, YOLO26n Re-ID inference, behavior analysis,
sensor synchronization, 6 AM - 6 PM hourly aggregation, and edge watchdog recovery.
"""

import os
import cv2
import time
import signal
import psutil
import datetime
import argparse
from pathlib import Path
from typing import Optional

from .config import CONFIG, EdgeConfig
from .detector import PoultryInferenceEngine, FrameInferenceResult
from .behavior import BehaviorAnalyzer, FrameBehaviorSummary
from .sensors import SensorHub, SensorReading
from .aggregator import HourlyTelemetryAggregator, HourlyAggregationRecord

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from local_store import copy_video_locally


class EdgePipelineManager:
    """
    Production-grade edge vision and telemetry pipeline.
    Runs continuously on Dell OptiPlex edge hub.
    """

    def __init__(
        self, 
        config: Optional[EdgeConfig] = None, 
        view_display: bool = False,
        record_video: Optional[bool] = None,
        output_scale: float = 0.5
    ):
        self.cfg = config or CONFIG
        self.view_display = view_display
        self.record_video = record_video if record_video is not None else self.cfg.vision.record_annotated_video
        self.output_scale = output_scale
        self.write_size = (1280, 720)  # fallback, updated by _ensure_video_writer
        self._running = False
        
        print("\n" + "=" * 65)
        print("    🐔 ChickenSee Edge AI Monitoring Pipeline — OptiPlex Hub")
        print("=" * 65)

        # 1. Initialize Components
        self.detector = PoultryInferenceEngine(self.cfg.vision)
        self.behavior_analyzer = BehaviorAnalyzer(self.cfg.behavior)
        self.sensor_hub = SensorHub(self.cfg.sensor)
        self.aggregator = HourlyTelemetryAggregator(self.cfg.schedule)

        # 2. State & Watchdog
        self.cap: Optional[cv2.VideoCapture] = None
        self.frame_idx = 0
        self._total_frames: int = 0
        self.consecutive_read_failures = 0
        self.reconnect_delay = self.cfg.vision.reconnect_delay_s

        # 3. Video Writer for Annotated Detection Videos
        self.video_writer: Optional[cv2.VideoWriter] = None
        self.current_video_slot: Optional[str] = None
        self.current_video_path: Optional[str] = None

        # 4. Setup signal handlers for graceful systemd/service termination
        signal.signal(signal.SIGINT, self._handle_exit_signal)
        signal.signal(signal.SIGTERM, self._handle_exit_signal)

    def _handle_exit_signal(self, signum, frame):
        print(f"\n[PIPELINE] Received termination signal ({signum}). Initiating graceful shutdown...")
        self._running = False

    def _open_video_stream(self) -> bool:
        """Opens video capture stream (RTSP, USB camera, or file) with resilience."""
        source = self.cfg.vision.video_source
        if self.cap is not None:
            self.cap.release()

        # Handle numeric string as integer webcam index
        if str(source).isdigit():
            source = int(source)

        print(f"[STREAM] Initializing video source: {source}")
        self.cap = cv2.VideoCapture(source)

        if not self.cap.isOpened():
            print(f"[STREAM] [WARNING] Could not open video source '{source}'")
            return False

        # Attempt buffer size optimization for low-latency RTSP feeds
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        print(f"[STREAM] Video source connected successfully.")
        # Attempt to read total frame count (works for file sources)
        self._total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        if self._total_frames > 0:
            fps_src = self.cap.get(cv2.CAP_PROP_FPS) or 20.0
            print(f"[STREAM] Total frames: {self._total_frames} (~{self._total_frames / fps_src / 60:.1f} min at {fps_src:.0f} FPS)")
        return True

    def _ensure_active_stream(self) -> bool:
        """Watchdog to ensure stream is open, auto-reconnecting on failures."""
        if self.cap is None or not self.cap.isOpened():
            print(f"[WATCHDOG] Reconnecting stream in {self.reconnect_delay:.1f}s...")
            time.sleep(self.reconnect_delay)
            self.reconnect_delay = min(
                self.reconnect_delay * 1.5, 
                self.cfg.vision.max_reconnect_delay_s
            )
            success = self._open_video_stream()
            if success:
                self.reconnect_delay = self.cfg.vision.reconnect_delay_s
            return success
        return True

    def _overlay_telemetry_ui(
        self, 
        frame: cv2.Mat, 
        inf_res: FrameInferenceResult, 
        beh_sum: FrameBehaviorSummary, 
        sensor: Optional[SensorReading]
    ):
        """Draws rich edge telemetry overlay on video frames for live viewing."""
        h, w = frame.shape[:2]

        # 1. Draw Detections & Behavior Badges
        for d in inf_res.detections:
            x1, y1, x2, y2 = d.box
            color = d.color_bgr
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # Behavior badge if chicken
            beh_text = ""
            if d.track_id is not None and d.track_id in beh_sum.per_bird_behaviors:
                beh_text = f" [{beh_sum.per_bird_behaviors[d.track_id]}]"

            label_text = f"#{d.track_id} {d.label}{beh_text} ({d.confidence:.2f})" if d.track_id else f"{d.label} ({d.confidence:.2f})"
            (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, max(0, y1 - 22)), (x1 + tw + 6, max(22, y1)), color, -1)
            cv2.putText(frame, label_text, (x1 + 3, max(16, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0) if sum(color) > 400 else (255, 255, 255), 1)

        # 2. Draw Top Telemetry Dashboard Bar
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 50), (20, 24, 30), -1)
        cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)

        # Top Bar Text
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        hud_left = f"TIME: {now_str} | BIRDS: {inf_res.total_birds} | ROOSTER: {inf_res.roosters} | HENS: {inf_res.hens_identified} | FPS: {inf_res.fps:.1f}"
        cv2.putText(frame, hud_left, (15, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)

        if sensor:
            stress_color = (0, 255, 0) if sensor.heat_stress_level == "Comfort" else ((0, 165, 255) if sensor.heat_stress_level == "Mild Alert" else (0, 0, 255))
            hud_sensor = f"TEMP: {sensor.temperature_c:.1f}C | RH: {sensor.humidity_pct:.0f}% | NH3: {sensor.ammonia_ppm:.1f}ppm | THI: {sensor.thi:.1f} [{sensor.heat_stress_level}]"
            cv2.putText(frame, hud_sensor, (15, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.48, stress_color, 1)

    def _ensure_video_writer(self, now_dt: datetime.datetime, frame_w: int, frame_h: int, fps: float):
        """Initializes or rolls over the hourly annotated detection video file."""
        slot_key = self.aggregator.get_hour_key(now_dt)
        if self.video_writer is not None and self.current_video_slot == slot_key:
            return

        # Close existing writer if rolling over
        self._close_video_writer()

        out_dir = Path(self.cfg.vision.output_video_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"detection_{now_dt.strftime('%Y-%m-%d_%H%M')}.mp4"
        self.current_video_path = str(out_dir / filename)
        self.current_video_slot = slot_key

        # Apply output scale (default 0.5 halves the resolution for smaller files)
        scale = self.output_scale
        write_w = max(2, int(frame_w * scale))
        write_h = max(2, int(frame_h * scale))
        self.write_size = (write_w, write_h)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        write_fps = max(5.0, fps)
        self.video_writer = cv2.VideoWriter(self.current_video_path, fourcc, write_fps, self.write_size)
        print(f"[RECORDER] Started recording detection video: {filename} ({write_w}x{write_h} @ {write_fps:.0f}fps)")

    def _close_video_writer(self):
        """Finalizes the video file so it is ready for immediate streaming & cloud sync."""
        if self.video_writer is not None:
            self.video_writer.release()
            self.video_writer = None
            if self.current_video_path and os.path.exists(self.current_video_path):
                # Remux / transcode to web-compatible H.264 for instant browser playback
                try:
                    import imageio_ffmpeg, subprocess
                    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                    h264_tmp = self.current_video_path + ".h264.mp4"
                    cmd = [ffmpeg_exe, "-y", "-i", self.current_video_path, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", h264_tmp]
                    res = subprocess.run(cmd, capture_output=True)
                    if res.returncode == 0 and os.path.exists(h264_tmp) and os.path.getsize(h264_tmp) > 0:
                        os.replace(h264_tmp, self.current_video_path)
                    elif os.path.exists(h264_tmp):
                        os.remove(h264_tmp)
                except Exception:
                    pass

                sz_mb = os.path.getsize(self.current_video_path) / 1024 / 1024
                base_name = os.path.basename(self.current_video_path)
                print(f"[RECORDER] Finalized detection video: {base_name} ({sz_mb:.2f} MB)")
                print(f"           → Saved in agent/data/ (watcher.js will automatically sync to Vercel!)")
                try:
                    local_copy = copy_video_locally(self.current_video_path)
                    if local_copy:
                        print(f"           → Local copy: {local_copy}")
                except Exception as e:
                    print(f"[RECORDER WARN] Failed to copy detection video to local storage: {e}")
            self.current_video_slot = None
            self.current_video_path = None

    def run(self, max_frames: Optional[int] = None):
        """
        Main execution loop.
        Monitors 6 AM - 6 PM operating hours, processes frames, and flushes hourly logs.
        """
        self._running = True
        self._open_video_stream()

        print("[PIPELINE] Edge AI Processing Pipeline active.")
        print(f"[PIPELINE] Operating schedule: {self.cfg.schedule.start_hour:02d}:00:00 - {self.cfg.schedule.end_hour:02d}:00:00 local time.")
        
        last_health_check = time.time()

        try:
            while self._running:
                now_dt = datetime.datetime.now()
                t_now = time.time()

                # -------------------------------------------------------------
                # 1. Enforce 6 AM - 6 PM Daily Operational Window
                # -------------------------------------------------------------
                if not self.aggregator.is_operational_hours(now_dt):
                    # Outside operational hours (18:00 - 06:00)
                    if self.cap is not None and self.cap.isOpened():
                        # Flush any remaining daytime metrics
                        self.aggregator.flush_hourly_summary(
                            all_bird_states=self.behavior_analyzer.get_all_bird_profiles()
                        )
                        print(f"[SCHEDULE] 18:00 PM reached. Closing camera stream & entering low-power standby mode.")
                        self.cap.release()
                        self.cap = None

                    # Sleep cleanly to avoid consuming CPU cycles on OptiPlex
                    print(f"[SCHEDULE] Standby mode active ({now_dt.strftime('%H:%M:%S')}). Waking in 30s to check schedule...")
                    time.sleep(30.0)
                    continue

                # -------------------------------------------------------------
                # 2. Watchdog Stream Ingestion
                # -------------------------------------------------------------
                if not self._ensure_active_stream():
                    continue

                ret, frame = self.cap.read()
                if not ret or frame is None:
                    self.consecutive_read_failures += 1
                    if self.consecutive_read_failures > 5:
                        print(f"[WATCHDOG] Frame read dropped ({self.consecutive_read_failures} fails). Reconnecting...")
                        self.cap.release()
                        self.cap = None
                        self.consecutive_read_failures = 0
                    time.sleep(0.1)
                    continue

                self.consecutive_read_failures = 0
                self.frame_idx += 1

                # -------------------------------------------------------------
                # 3. Sensor-Vision Temporal Synchronization
                # -------------------------------------------------------------
                sensor_reading = self.sensor_hub.get_synchronized_reading(frame_timestamp=t_now)

                # -------------------------------------------------------------
                # 4. Inference & Poultry Re-ID
                # -------------------------------------------------------------
                inf_result = self.detector.process_frame(frame, timestamp=t_now)

                # -------------------------------------------------------------
                # 5. Behavior Tracking & Spatial Analysis
                # -------------------------------------------------------------
                beh_summary = self.behavior_analyzer.analyze_frame(inf_result, current_time=t_now)

                # -------------------------------------------------------------
                # 6. Hourly Aggregation & Database Flush
                # -------------------------------------------------------------
                all_birds = self.behavior_analyzer.get_all_bird_profiles()
                self.aggregator.ingest_frame(
                    inference_result=inf_result,
                    behavior_summary=beh_summary,
                    sensor_reading=sensor_reading,
                    all_bird_states=all_birds
                )

                # -------------------------------------------------------------
                # 7. Memory Leak Prevention & System Telemetry
                # -------------------------------------------------------------
                if self.frame_idx % self.cfg.memory_cleanup_interval_frames == 0:
                    import gc
                    gc.collect()

                if t_now - last_health_check >= self.cfg.health_check_interval_s or self.frame_idx % 100 == 0:
                    last_health_check = t_now
                    cpu_p = psutil.cpu_percent()
                    ram_mb = psutil.Process().memory_info().rss / 1024 / 1024
                    total_frames_str = f"/{self._total_frames}" if self._total_frames else ""
                    pct = f" ({self.frame_idx / self._total_frames * 100:.1f}%)" if self._total_frames else ""
                    print(f"[PROGRESS] Frame {self.frame_idx}{total_frames_str}{pct} | FPS: {inf_result.fps:.1f} | CPU: {cpu_p:.0f}% | RAM: {ram_mb:.0f}MB | Birds: {inf_result.total_birds}")

                # -------------------------------------------------------------
                # 8. Annotated Video Recording & Live UI Visualization
                # -------------------------------------------------------------
                if self.record_video or self.view_display:
                    annotated_frame = frame.copy()
                    self._overlay_telemetry_ui(annotated_frame, inf_result, beh_summary, sensor_reading)

                    # Write frame to MP4 file (scaled to write_size)
                    if self.record_video:
                        h, w = frame.shape[:2]
                        fps = self.cfg.vision.video_fps_target
                        self._ensure_video_writer(now_dt, w, h, fps)
                        if self.video_writer is not None:
                            write_frame = cv2.resize(annotated_frame, self.write_size, interpolation=cv2.INTER_AREA)
                            self.video_writer.write(write_frame)

                    # Display on screen if requested
                    if self.view_display:
                        cv2.imshow("ChickenSee Edge Hub", annotated_frame)
                        key = cv2.waitKey(1) & 0xFF
                        if key in (27, ord('q')):
                            print("[PIPELINE] Exit requested by user via UI.")
                            break

                # Frame limit for unit testing / batch evaluation
                if max_frames and self.frame_idx >= max_frames:
                    print(f"[PIPELINE] Reached target limit of {max_frames} frames.")
                    break

        finally:
            self._shutdown()

    def _shutdown(self):
        """Final cleanup and persistence on pipeline termination."""
        print("\n[PIPELINE] Stopping Edge Pipeline...")
        if self.cap is not None:
            self.cap.release()
        if self.view_display:
            cv2.destroyAllWindows()

        self._close_video_writer()
        self.sensor_hub.stop()

        # Flush any unflushed hourly records to SQLite/CSV/JSON
        print("[PIPELINE] Committing final session telemetry...")
        self.aggregator.flush_hourly_summary(
            all_bird_states=self.behavior_analyzer.get_all_bird_profiles()
        )
        print("[PIPELINE] Edge AI Pipeline gracefully shut down.")


def main():
    parser = argparse.ArgumentParser(description="ChickenSee Edge AI Pipeline - Dell OptiPlex Hub")
    parser.add_argument("--source", type=str, default=None, help="Video stream URL, device index, or file path")
    parser.add_argument("--view", action="store_true", help="Display live OpenCV overlay window")
    parser.add_argument("--record", action="store_true", default=None, help="Force enable annotated video recording to disk")
    parser.add_argument("--no-record", action="store_true", help="Disable annotated video recording")
    parser.add_argument("--output-scale", type=float, default=0.5, help="Scale factor for output annotated video resolution (default: 0.5 = half res)")
    parser.add_argument("--frames", type=int, default=None, help="Stop after N frames (useful for verification/benchmarking)")
    parser.add_argument("--force-start", action="store_true", help="Bypass 6 AM - 6 PM check for off-hours testing")
    args = parser.parse_args()

    if args.source:
        CONFIG.vision.video_source = args.source

    if args.force_start:
        # Extend operational window for immediate testing regardless of local clock
        CONFIG.schedule.start_hour = 0
        CONFIG.schedule.end_hour = 24

    record_opt = None
    if args.no_record:
        record_opt = False
    elif args.record:
        record_opt = True

    pipeline = EdgePipelineManager(view_display=args.view, record_video=record_opt, output_scale=args.output_scale)
    pipeline.run(max_frames=args.frames)


if __name__ == "__main__":
    main()

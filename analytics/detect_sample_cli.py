#!/usr/bin/env python3
"""
analytics/detect_sample_cli.py
CLI tool to run YOLO26 Nano + ByteTrack detection on a sample video file
and output a web-compatible H.264 (avc1) annotated video with bounding boxes,
IDs, leg band tags, and live telemetry HUD.
"""

import sys
import os
import json
import argparse
import tempfile
import subprocess
import time
from pathlib import Path

# Ensure root directory is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import cv2
import imageio_ffmpeg
from analytics.tracker import ZampenYOLOTracker


def run_detection(
    input_path: str,
    output_path: str,
    stride: int = 1,
    max_duration_sec: float = 0.0,
):
    if not os.path.exists(input_path):
        print(json.dumps({"error": f"Input file not found: {input_path}"}), file=sys.stderr)
        sys.exit(1)

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        print(json.dumps({"error": f"Failed to open video: {input_path}"}), file=sys.stderr)
        sys.exit(1)

    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

    if max_duration_sec > 0:
        max_frames = int(max_duration_sec * fps)
        if total_frames <= 0 or total_frames > max_frames:
            total_frames = max_frames

    print(json.dumps({
        "status": "started",
        "fps": fps,
        "width": w,
        "height": h,
        "total_frames": total_frames,
    }), flush=True)

    tracker = ZampenYOLOTracker()

    # Intermediate raw mp4v file in temp directory
    temp_dir = tempfile.mkdtemp(prefix="chickensee_cli_")
    raw_staging = os.path.join(temp_dir, "staging_raw.mp4")

    # Output fps accounts for stride if we step
    effective_fps = fps / stride if stride > 1 else fps
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(raw_staging, fourcc, effective_fps, (w, h))

    frame_idx = 0
    written_frames = 0
    start_time = time.time()

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if max_duration_sec > 0 and frame_idx >= total_frames:
                break

            if stride <= 1 or (frame_idx % stride == 0):
                frame_out = tracker.process_frame(frame, frame_idx, fps)
                annotated = tracker.annotate_frame(frame, frame_out)
                writer.write(annotated)
                written_frames += 1

            frame_idx += 1

            if frame_idx % 15 == 0:
                elapsed = time.time() - start_time
                pct = int((frame_idx / total_frames) * 100) if total_frames > 0 else 0
                pct = min(99, max(1, pct))
                print(json.dumps({
                    "progress": pct,
                    "frame": frame_idx,
                    "total": total_frames,
                    "fps_speed": round(frame_idx / elapsed, 1) if elapsed > 0 else 0,
                }), flush=True)

    finally:
        cap.release()
        writer.release()

    if written_frames == 0:
        print(json.dumps({"error": "No frames written."}), file=sys.stderr)
        sys.exit(1)

    print(json.dumps({"status": "transcoding", "written_frames": written_frames}), flush=True)

    # Transcode mp4v -> standard H.264 (avc1) + yuv420p + faststart for browser playback
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    cmd = [
        ffmpeg_exe, "-y",
        "-i", raw_staging,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-movflags", "+faststart",
        output_path,
    ]

    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        # Fallback: copy staging directly
        import shutil
        shutil.copyfile(raw_staging, output_path)

    # Clean up temp files
    try:
        if os.path.exists(raw_staging):
            os.remove(raw_staging)
        os.rmdir(temp_dir)
    except Exception:
        pass

    out_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
    print(json.dumps({
        "status": "completed",
        "output_path": output_path,
        "output_size_bytes": out_size,
        "frames_processed": written_frames,
    }), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run YOLO poultry detection on video")
    parser.add_argument("--input", "-i", required=True, help="Input video file path")
    parser.add_argument("--output", "-o", required=True, help="Output detected video file path")
    parser.add_argument("--stride", "-s", type=int, default=1, help="Process every Nth frame")
    parser.add_argument("--max-duration", "-m", type=float, default=0.0, help="Max duration in seconds (0 for all)")
    args = parser.parse_args()

    run_detection(
        input_path=args.input,
        output_path=args.output,
        stride=args.stride,
        max_duration_sec=args.max_duration,
    )

"""
edge/config.py - Centralized Configuration for ChickenSee Edge AI Pipeline.
Optimized for local deployment on Dell OptiPlex edge hardware.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field

# Base paths
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "agent" / "data"
REPORTS_DIR = DATA_DIR / "reports"

DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class VisionConfig:
    # Model Weights
    model_path: str = os.getenv("MODEL_PATH", str(ROOT_DIR / "custom_poultry.pt"))
    tracker_config: str = os.getenv("TRACKER_CONFIG", str(ROOT_DIR / "bytetrack_poultry.yaml"))
    
    # Inference Tuning for Dell OptiPlex CPU/GPU
    inference_size: int = int(os.getenv("INFERENCE_SIZE", "640"))
    conf_threshold: float = float(os.getenv("CONF_THRESHOLD", "0.15"))
    iou_threshold: float = float(os.getenv("IOU_THRESHOLD", "0.45"))
    num_cpu_threads: int = int(os.getenv("CPU_THREADS", str(os.cpu_count() or 8)))
    
    # Preprocessing
    target_width: int = int(os.getenv("TARGET_WIDTH", "640"))  # downscale input if needed
    
    # Video Ingestion
    # Can be RTSP URL (e.g. "rtsp://admin:pass@192.168.1.100:554/ch01"), webcam index (0), or file path
    video_source: str = os.getenv(
        "VIDEO_SOURCE", 
        str(DATA_DIR / "48f95267-a3be-45a8-aea3-cccc8c413297.mp4")
    )
    video_fps_target: float = float(os.getenv("FPS_TARGET", "15.0"))
    reconnect_delay_s: float = 2.0
    max_reconnect_delay_s: float = 30.0
    
    # Annotated Video Recording (Saves video with bounding boxes & telemetry HUD)
    record_annotated_video: bool = os.getenv("RECORD_ANNOTATED", "1").lower() in ("1", "true", "yes")
    output_video_dir: str = str(DATA_DIR)


@dataclass
class BehaviorConfig:
    # Interaction Proximity (pixels in scaled frame)
    # Feeder / Drinker proximity threshold for eating / drinking detection
    feeder_proximity_px: float = float(os.getenv("FEEDER_PROXIMITY_PX", "90.0"))
    drinker_proximity_px: float = float(os.getenv("DRINKER_PROXIMITY_PX", "90.0"))
    
    # Velocity thresholds (pixels per second)
    # Resting / Inactive: below this speed
    resting_velocity_px_s: float = float(os.getenv("RESTING_VELOCITY_PX_S", "4.0"))
    # Agitated / Running: sudden panic or flight speed
    agitated_velocity_px_s: float = float(os.getenv("AGITATED_VELOCITY_PX_S", "70.0"))
    
    # Behavior state debounce window in seconds
    min_event_duration_s: float = 1.0


@dataclass
class SensorConfig:
    # Ingestion Source: "serial", "mqtt", "http", or "simulation"
    source_type: str = os.getenv("SENSOR_SOURCE", "simulation")
    serial_port: str = os.getenv("SENSOR_SERIAL_PORT", "COM3")
    serial_baud: int = int(os.getenv("SENSOR_BAUD", "9600"))
    poll_interval_s: float = float(os.getenv("SENSOR_POLL_INTERVAL_S", "1.0"))
    sync_tolerance_s: float = 2.0  # max temporal difference for frame-sensor pairing


@dataclass
class ScheduleConfig:
    # Daily Operational Window (Local Server Time)
    start_hour: int = int(os.getenv("START_HOUR", "6"))    # 06:00:00
    end_hour: int = int(os.getenv("END_HOUR", "18"))        # 18:00:00
    
    # Flush interval in seconds (default: 3600s = 1 hour on the hour)
    # Can be overridden for testing/dev (e.g., 60s for rapid verification)
    hourly_flush_seconds: int = int(os.getenv("HOURLY_FLUSH_SECONDS", "3600"))
    
    # SQLite Database File: written into agent/data so watcher.js auto-syncs to Vercel
    db_path: str = os.getenv("DB_PATH", str(DATA_DIR / "chicken_telemetry.db"))
    reports_dir: str = str(REPORTS_DIR)


@dataclass
class EdgeConfig:
    vision: VisionConfig = field(default_factory=VisionConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    sensor: SensorConfig = field(default_factory=SensorConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    
    # System Telemetry
    health_check_interval_s: float = 60.0
    memory_cleanup_interval_frames: int = 500


# Global singleton configuration
CONFIG = EdgeConfig()

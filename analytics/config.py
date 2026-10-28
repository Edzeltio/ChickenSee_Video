"""Environment-driven configuration for local chicken video analytics."""

import os
from pathlib import Path
from pydantic import BaseModel, Field

from model_registry import resolve_model_path

# Base directories
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "agent" / "data"
VIDEO_INPUT_DIR = Path(os.getenv("VIDEO_INPUT_DIR", str(DATA_DIR)))
SENSOR_DB_PATH = Path(os.getenv("SENSOR_DB_PATH", str(DATA_DIR / "chicken_telemetry.db")))
ANALYTICS_DB_PATH = Path(os.getenv("ANALYTICS_DB_PATH", str(BASE_DIR / "zampen_analytics.db")))


class AnalyticsConfig(BaseModel):
    # Model & Tracker
    model_name: str = Field(
        default_factory=lambda: os.getenv("MODEL_NAME", "yolo26n.pt"),
        description="Legacy model name; the active registry or MODEL_PATH selects runtime weights",
    )
    custom_weights_path: str = Field(
        default_factory=lambda: resolve_model_path(
            PROJECT_ROOT,
            env_path=os.getenv("MODEL_PATH"),
            fallback_path=PROJECT_ROOT / "custom_poultry.pt",
        ),
        description="Optional fine-tuned poultry weights fallback"
    )
    tracker_config: str = Field(
        default_factory=lambda: os.getenv(
            "TRACKER_CONFIG",
            str(PROJECT_ROOT / "bytetrack_poultry.yaml")
            if (PROJECT_ROOT / "bytetrack_poultry.yaml").exists()
            else "bytetrack.yaml",
        ),
        description="Ultralytics tracker YAML; ByteTrack is the lightweight default"
    )
    device: str = Field(
        default_factory=lambda: os.getenv("INFERENCE_DEVICE", "auto"),
        description="auto, cpu, mps, or CUDA device index such as 0",
    )
    conf_threshold: float = Field(
        default_factory=lambda: float(os.getenv("CONF_THRESHOLD", "0.15")),
        description="Detector floor; keep at or below ByteTrack track_low_thresh for recovery matches",
    )
    iou_threshold: float = Field(
        default_factory=lambda: float(os.getenv("IOU_THRESHOLD", "0.45")),
        description="NMS IoU threshold",
    )
    imgsz: int = Field(
        default_factory=lambda: int(os.getenv("INFERENCE_SIZE", "640")),
        description="Inference input image resolution",
    )
    frame_stride: int = Field(
        default_factory=lambda: int(os.getenv("FRAME_STRIDE", "2")),
        description="Process every Nth frame (2 = half of source FPS)",
    )

    # Processing & video discovery
    chunk_minutes: int = Field(default_factory=lambda: int(os.getenv("CHUNK_MINUTES", "30")), description="Maximum duration of each analysis interval")
    poll_interval_s: int = Field(default_factory=lambda: int(os.getenv("VIDEO_POLL_INTERVAL_S", "1")), description="Worker video discovery fallback interval in seconds")
    stability_threshold_s: float = Field(
        default_factory=lambda: float(os.getenv("FILE_STABILITY_THRESHOLD_S", "2.0")),
        description="Required idle duration in seconds before processing a newly written video"
    )

    # Paths
    video_dir: str = str(VIDEO_INPUT_DIR)
    sensor_db_url: str = f"sqlite:///{SENSOR_DB_PATH.as_posix()}"
    analytics_db_url: str = f"sqlite:///{ANALYTICS_DB_PATH.as_posix()}"

    # Behavior Detection Thresholds (pixels & seconds for 640x360 or 1280x720 video)
    feeder_zone_proximity_px: float = Field(
        default_factory=lambda: float(os.getenv("FEEDER_ZONE_PROXIMITY_PX", "95")),
        description="Proximity threshold to feeder bounding box",
    )
    waterer_zone_proximity_px: float = Field(
        default_factory=lambda: float(os.getenv("WATERER_ZONE_PROXIMITY_PX", "95")),
        description="Proximity threshold to waterer bounding box",
    )
    
    # Inactivity Thresholds
    inactivity_velocity_threshold_px_s: float = Field(
        default_factory=lambda: float(os.getenv("INACTIVITY_VELOCITY_PX_S", "3.5")),
        description="Centroid displacement below which bird is stationary",
    )
    inactivity_sustained_duration_s: float = Field(
        default_factory=lambda: float(os.getenv("INACTIVITY_DURATION_S", "180")),
        description="Sustained stationary seconds to flag lethargy",
    )
    
    # Reduced Eating Thresholds
    reduced_eating_motion_threshold_px_s: float = Field(
        default=4.0, 
        description="Minimum localized jitter/pecking motion required while in feeder zone"
    )
    
    # Feather Pecking Detection Thresholds
    # Tuned for 640-wide video at 15 fps with ByteTrack smoothed centroids.
    # At 640px width a bird body is ~40-60px; 22 px/s ≈ brisk directional charge.
    feather_pecking_proximity_px: float = Field(
        default_factory=lambda: float(os.getenv("FEATHER_PECKING_PROXIMITY_PX", "60")),
        description="Inter-bird centroid distance threshold for close interaction",
    )
    feather_pecking_approach_speed_px_s: float = Field(
        default_factory=lambda: float(os.getenv("FEATHER_PECKING_APPROACH_PX_S", "22")),
        description="Minimum aggressor approach speed (px/s) indicating aggression",
    )
    feather_pecking_escape_speed_px_s: float = Field(
        default_factory=lambda: float(os.getenv("FEATHER_PECKING_ESCAPE_PX_S", "18")),
        description="Minimum victim escape acceleration burst (px/s)",
    )

    # Server API
    api_host: str = Field(
        default_factory=lambda: os.getenv("ANALYTICS_API_HOST", "127.0.0.1"),
        description="FastAPI host; localhost is the safe default",
    )
    api_port: int = Field(
        default_factory=lambda: int(os.getenv("ANALYTICS_API_PORT", "8000")),
        description="FastAPI port",
    )


CONFIG = AnalyticsConfig()
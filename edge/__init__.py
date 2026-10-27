"""
ChickenSee Edge AI Monitoring System.
Modular components for real-time YOLO26n poultry detection, ByteTrack Re-ID,
behavior classification, sensor synchronization, and hourly telemetry aggregation.
"""

from .config import CONFIG, EdgeConfig, VisionConfig, BehaviorConfig, SensorConfig, ScheduleConfig
from .detector import PoultryInferenceEngine, Detection, FrameInferenceResult
from .behavior import BehaviorAnalyzer, BirdBehaviorState, FrameBehaviorSummary
from .sensors import SensorHub, SensorReading
from .aggregator import HourlyTelemetryAggregator, HourlyAggregationRecord
from .pipeline import EdgePipelineManager

__all__ = [
    "CONFIG",
    "EdgeConfig",
    "VisionConfig",
    "BehaviorConfig",
    "SensorConfig",
    "ScheduleConfig",
    "PoultryInferenceEngine",
    "Detection",
    "FrameInferenceResult",
    "BehaviorAnalyzer",
    "BirdBehaviorState",
    "FrameBehaviorSummary",
    "SensorHub",
    "SensorReading",
    "HourlyTelemetryAggregator",
    "HourlyAggregationRecord",
    "EdgePipelineManager",
]

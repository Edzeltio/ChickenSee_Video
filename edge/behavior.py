"""
edge/behavior.py - Poultry Behavior Tracking & Spatial Analysis Engine.
Analyzes feeder/drinker interactions, kinematics (resting vs active vs agitated),
and maintains per-bird behavior time-budgets and activity profiles.
"""

import math
import time
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import deque

from .config import CONFIG, BehaviorConfig
from .detector import Detection, FrameInferenceResult


@dataclass
class BirdBehaviorState:
    track_id: int
    label: str
    bird_type: str                   # "hen" | "rooster"
    current_behavior: str = "Resting" # "Feeding", "Drinking", "Active", "Resting", "Agitated"
    behavior_confidence: float = 0.8
    
    # Cumulative time in seconds
    feeding_sec: float = 0.0
    drinking_sec: float = 0.0
    active_sec: float = 0.0
    resting_sec: float = 0.0
    agitated_sec: float = 0.0
    
    # Discrete bouts (visits)
    feeding_bouts: int = 0
    drinking_bouts: int = 0
    
    # Kinematics
    velocity_px_s: float = 0.0
    total_distance_px: float = 0.0
    last_centroid: Optional[Tuple[int, int]] = None
    last_timestamp: float = 0.0
    first_seen_timestamp: float = 0.0
    recent_velocities: deque = field(default_factory=lambda: deque(maxlen=15))
    
    # Inactivity / Health Monitoring
    continuous_inactivity_sec: float = 0.0
    is_lethargic_alert: bool = False


@dataclass
class FrameBehaviorSummary:
    timestamp: float
    birds_count: int
    feeding_count: int = 0
    drinking_count: int = 0
    active_count: int = 0
    resting_count: int = 0
    agitated_count: int = 0
    flock_clustering_index: float = 0.0  # Normalized flock spatial density
    per_bird_behaviors: Dict[int, str] = field(default_factory=dict) # track_id -> behavior name


class BehaviorAnalyzer:
    """
    Real-time spatial proximity & kinematic behavior analyzer.
    Tracks individual bird activity budgets across video streams.
    """

    def __init__(self, behavior_config: Optional[BehaviorConfig] = None):
        self.cfg = behavior_config or CONFIG.behavior
        self.bird_states: Dict[int, BirdBehaviorState] = {}
        self._last_frame_time: Optional[float] = None
        self.max_inactive_duration_s = 600.0  # 10 minutes without sight before state prune

    def _calc_distance(self, p1: Tuple[int, int], p2: Tuple[int, int]) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    def _box_distance(self, box1: List[int], box2: List[int]) -> float:
        """Calculate minimum Euclidean distance between two 2D bounding boxes."""
        x1_min, y1_min, x1_max, y1_max = box1
        x2_min, y2_min, x2_max, y2_max = box2

        # Check for intersection / overlap
        dx = max(0, max(x1_min, x2_min) - min(x1_max, x2_max))
        dy = max(0, max(y1_min, y2_min) - min(y1_max, y2_max))
        return math.hypot(dx, dy)

    def analyze_frame(
        self, 
        inference_result: FrameInferenceResult, 
        current_time: Optional[float] = None
    ) -> FrameBehaviorSummary:
        """
        Analyzes bird detections against infrastructure and historical motion vectors.
        """
        t_now = current_time or inference_result.timestamp
        dt = (t_now - self._last_frame_time) if self._last_frame_time is not None else (1.0 / 15.0)
        dt = max(0.001, min(1.0, dt)) # Clamp dt to sensible limits
        self._last_frame_time = t_now

        feeders = [d for d in inference_result.infrastructure if d.label == "Feeder"]
        drinkers = [d for d in inference_result.infrastructure if d.label == "Drinker"]

        frame_summary = FrameBehaviorSummary(
            timestamp=t_now,
            birds_count=len(inference_result.birds)
        )

        active_track_ids = set()

        for bird in inference_result.birds:
            tid = bird.track_id
            if tid is None:
                continue

            active_track_ids.add(tid)

            # Initialize bird state if new track
            if tid not in self.bird_states:
                self.bird_states[tid] = BirdBehaviorState(
                    track_id=tid,
                    label=bird.label,
                    bird_type=bird.type,
                    first_seen_timestamp=t_now,
                    last_timestamp=t_now,
                    last_centroid=bird.centroid
                )

            state = self.bird_states[tid]
            state.label = bird.label  # Update label if Re-ID locked
            state.bird_type = bird.type

            # -------------------------------------------------------------
            # 1. Kinematics (Centroid Displacement Velocity)
            # -------------------------------------------------------------
            velocity = 0.0
            if state.last_centroid is not None:
                dist = self._calc_distance(bird.centroid, state.last_centroid)
                velocity = dist / dt
                state.total_distance_px += dist
                state.recent_velocities.append(velocity)

            state.last_centroid = bird.centroid
            state.last_timestamp = t_now
            avg_velocity = sum(state.recent_velocities) / len(state.recent_velocities) if state.recent_velocities else 0.0
            state.velocity_px_s = avg_velocity

            # -------------------------------------------------------------
            # 2. Spatial Interaction with Infrastructure
            # -------------------------------------------------------------
            is_feeding = False
            for f in feeders:
                d_feeder = self._box_distance(bird.box, f.box)
                if d_feeder <= self.cfg.feeder_proximity_px:
                    is_feeding = True
                    break

            is_drinking = False
            for d in drinkers:
                d_drinker = self._box_distance(bird.box, d.box)
                if d_drinker <= self.cfg.drinker_proximity_px:
                    is_drinking = True
                    break

            # -------------------------------------------------------------
            # 3. Behavior Classification State Machine
            # -------------------------------------------------------------
            previous_behavior = state.current_behavior

            if is_feeding:
                new_behavior = "Feeding"
                state.feeding_sec += dt
                if previous_behavior != "Feeding":
                    state.feeding_bouts += 1
                state.continuous_inactivity_sec = 0.0

            elif is_drinking:
                new_behavior = "Drinking"
                state.drinking_sec += dt
                if previous_behavior != "Drinking":
                    state.drinking_bouts += 1
                state.continuous_inactivity_sec = 0.0

            elif avg_velocity >= self.cfg.agitated_velocity_px_s:
                new_behavior = "Agitated"
                state.agitated_sec += dt
                state.continuous_inactivity_sec = 0.0

            elif avg_velocity >= self.cfg.resting_velocity_px_s:
                new_behavior = "Active"
                state.active_sec += dt
                state.continuous_inactivity_sec = 0.0

            else:
                new_behavior = "Resting"
                state.resting_sec += dt
                state.continuous_inactivity_sec += dt
                # Alert if bird has been continuously inactive for over 15 minutes (900s)
                if state.continuous_inactivity_sec > 900.0:
                    state.is_lethargic_alert = True

            state.current_behavior = new_behavior
            frame_summary.per_bird_behaviors[tid] = new_behavior

            # Accumulate frame counts
            if new_behavior == "Feeding":
                frame_summary.feeding_count += 1
            elif new_behavior == "Drinking":
                frame_summary.drinking_count += 1
            elif new_behavior == "Active":
                frame_summary.active_count += 1
            elif new_behavior == "Resting":
                frame_summary.resting_count += 1
            elif new_behavior == "Agitated":
                frame_summary.agitated_count += 1

        # -----------------------------------------------------------------
        # 4. Flock Spatial Dispersion (Clustering Index)
        # -----------------------------------------------------------------
        centroids = [b.centroid for b in inference_result.birds]
        if len(centroids) >= 2:
            pair_dists = []
            for i in range(len(centroids)):
                for j in range(i + 1, len(centroids)):
                    pair_dists.append(self._calc_distance(centroids[i], centroids[j]))
            mean_dist = sum(pair_dists) / len(pair_dists) if pair_dists else 0.0
            # Normalized clustering index (inverse distance scaled)
            frame_summary.flock_clustering_index = round(1000.0 / (mean_dist + 10.0), 2)

        # -----------------------------------------------------------------
        # 5. Prune Stale Bird States (Memory Safety)
        # -----------------------------------------------------------------
        self._prune_stale_tracks(t_now)

        return frame_summary

    def _prune_stale_tracks(self, current_time: float):
        """Removes bird states unseen for longer than max_inactive_duration_s."""
        cutoff = current_time - self.max_inactive_duration_s
        stale_tids = [tid for tid, s in self.bird_states.items() if s.last_timestamp < cutoff]
        for tid in stale_tids:
            self.bird_states.pop(tid, None)

    def get_all_bird_profiles(self) -> List[BirdBehaviorState]:
        """Returns list of all tracked bird behavior profiles."""
        return list(self.bird_states.values())

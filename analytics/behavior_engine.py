"""
analytics/behavior_engine.py
Behavior Detection & Spatial Kinematics Engine for ZAMPEN Layer Chickens.
Implements specific computer vision behavior logic:
  1. Reduce Eating (feeder proximity + localized motion drop)
  2. Drinking (waterer visit duration and bout frequency)
  3. Inactivity (minimal coordinate displacement over sustained time intervals)
  4. Feather Pecking (high-velocity approach + close proximity overlap + victim escape burst)
"""

import math
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from collections import deque

from .config import CONFIG
from .tracker import TrackedDetection, TrackerFrameOutput


@dataclass
class BirdTrackState:
    track_id: int
    label: str
    bird_type: str
    last_centroid: Optional[Tuple[int, int]] = None
    last_timestamp_s: float = 0.0
    
    # Kinematics
    velocity_px_s: float = 0.0
    instant_velocity_px_s: float = 0.0          # Single-frame velocity (used for pecking spikes)
    recent_velocities: deque = field(default_factory=lambda: deque(maxlen=20))
    total_displacement_px: float = 0.0

    # 1. Reduce Eating metrics
    is_in_feeder_zone: bool = False
    feeding_time_s: float = 0.0
    feeding_bouts: int = 0
    feeder_stationary_time_s: float = 0.0  # In zone but not actively pecking
    reduced_eating_events: int = 0

    # 2. Drinking metrics
    is_in_waterer_zone: bool = False
    drinking_time_s: float = 0.0
    drinking_bouts: int = 0

    # 3. Inactivity metrics
    continuous_stationary_s: float = 0.0
    total_inactivity_s: float = 0.0
    is_lethargic_flag: bool = False

    # 4. Feather Pecking interactions
    pecking_aggressor_count: int = 0
    pecking_victim_count: int = 0


@dataclass
class BehaviorChunkSummary:
    unique_birds: int
    avg_birds_present: float
    flock_mobility_px_s: float
    
    # 1. Reduce Eating
    feeding_time_total_s: float
    feeding_bouts: int
    reduced_eating_events: int
    reduced_eating_index: float           # 0.0 to 1.0 ratio
    
    # 2. Drinking
    drinking_time_total_s: float
    drinking_bouts: int
    drinking_frequency_per_bird: float
    
    # 3. Inactivity
    inactivity_time_total_s: float
    inactivity_pct: float
    lethargic_birds_flagged: int
    
    # 4. Feather Pecking
    feather_pecking_events: int
    agitation_score: float

    # Per-bird breakdown records
    bird_records: List[Dict[str, Any]]


class BehaviorAnalysisEngine:
    """
    Computes spatial kinematics, infrastructure interactions, and behavior events
    across 30-minute chunk intervals using persistent ByteTrack trajectories.
    """

    def __init__(self):
        self.cfg = CONFIG
        self.bird_states: Dict[int, BirdTrackState] = {}
        self.feather_pecking_events: int = 0
        self.total_frames_in_chunk: int = 0
        self.bird_frame_counts: List[int] = []
        self.pecking_cooldowns: Dict[Tuple[int, int], float] = {}
        self.current_chunk_elapsed_s: float = 0.0

    def reset_chunk(self):
        """Resets tracking accumulators for a new 30-minute interval."""
        self.bird_states.clear()
        self.feather_pecking_events = 0
        self.total_frames_in_chunk = 0
        self.bird_frame_counts.clear()
        self.pecking_cooldowns.clear()
        self.current_chunk_elapsed_s = 0.0

    @staticmethod
    def _calc_euclidean(p1: Tuple[int, int], p2: Tuple[int, int]) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    @staticmethod
    def _box_distance(box1: List[int], box2: List[int]) -> float:
        """Minimum edge-to-edge distance between two bounding boxes (0 if intersecting)."""
        x1_min, y1_min, x1_max, y1_max = box1
        x2_min, y2_min, x2_max, y2_max = box2
        dx = max(0, max(x1_min, x2_min) - min(x1_max, x2_max))
        dy = max(0, max(y1_min, y2_min) - min(y1_max, y2_max))
        return math.hypot(dx, dy)

    def process_frame(self, frame_out: TrackerFrameOutput, dt: float):
        """
        Processes single-frame ByteTrack detections and spatial coordinates.
        dt: Delta time between processed frames in seconds.
        """
        if dt <= 0:
            dt = 1.0 / 15.0

        self.total_frames_in_chunk += 1
        self.current_chunk_elapsed_s += dt
        current_birds = frame_out.birds
        self.bird_frame_counts.append(len(current_birds))

        # -------------------------------------------------------------
        # Step 1: Update Kinematics & States for each Track
        # -------------------------------------------------------------
        for bird in current_birds:
            tid = bird.track_id
            if tid is None:
                continue

            if tid not in self.bird_states:
                self.bird_states[tid] = BirdTrackState(
                    track_id=tid,
                    label=bird.bird_label,
                    bird_type="rooster" if bird.class_name == "Rooster" else "hen",
                    last_centroid=bird.centroid,
                    last_timestamp_s=frame_out.timestamp_s
                )

            state = self.bird_states[tid]
            state.label = bird.bird_label

            # Kinematic velocity calculation (displacement / dt)
            velocity = 0.0
            if state.last_centroid is not None:
                dist = self._calc_euclidean(bird.centroid, state.last_centroid)
                velocity = dist / dt
                state.total_displacement_px += dist
                state.recent_velocities.append(velocity)

            state.last_centroid = bird.centroid
            state.last_timestamp_s = frame_out.timestamp_s
            # Smoothed average velocity (used for inactivity / eating metrics)
            avg_vel = sum(state.recent_velocities) / len(state.recent_velocities) if state.recent_velocities else 0.0
            state.velocity_px_s = avg_vel
            # Instantaneous velocity (single frame — used for pecking spike detection)
            state.instant_velocity_px_s = velocity

            # -------------------------------------------------------------
            # Metric 1: Reduce Eating Logic
            # -------------------------------------------------------------
            # Check proximity to feeder zones
            near_feeder = False
            for feeder in frame_out.feeders:
                d = self._box_distance(bird.box, feeder.box)
                if d <= self.cfg.feeder_zone_proximity_px:
                    near_feeder = True
                    break

            if near_feeder:
                state.feeding_time_s += dt
                if not state.is_in_feeder_zone:
                    state.feeding_bouts += 1
                    state.is_in_feeder_zone = True

                # Check for Reduced Eating: bird is in feeder zone, but localized
                # pecking motion/velocity is below threshold (standing idle without feeding)
                if avg_vel < self.cfg.reduced_eating_motion_threshold_px_s:
                    state.feeder_stationary_time_s += dt
                    # If idle in feeder zone for > 15 consecutive seconds, flag reduced eating event
                    if state.feeder_stationary_time_s >= 15.0:
                        state.reduced_eating_events += 1
                        state.feeder_stationary_time_s = 0.0
            else:
                state.is_in_feeder_zone = False
                state.feeder_stationary_time_s = 0.0

            # -------------------------------------------------------------
            # Metric 2: Drinking Logic
            # -------------------------------------------------------------
            near_drinker = False
            for drinker in frame_out.drinkers:
                d = self._box_distance(bird.box, drinker.box)
                if d <= self.cfg.waterer_zone_proximity_px:
                    near_drinker = True
                    break

            if near_drinker:
                state.drinking_time_s += dt
                if not state.is_in_waterer_zone:
                    state.drinking_bouts += 1
                    state.is_in_waterer_zone = True
            else:
                state.is_in_waterer_zone = False

            # -------------------------------------------------------------
            # Metric 3: Inactivity & Lethargy Logic
            # -------------------------------------------------------------
            # Inactivity: centroid displacement < inactivity_threshold
            if avg_vel < self.cfg.inactivity_velocity_threshold_px_s:
                state.continuous_stationary_s += dt
                state.total_inactivity_s += dt
                if state.continuous_stationary_s >= self.cfg.inactivity_sustained_duration_s:
                    state.is_lethargic_flag = True
            else:
                state.continuous_stationary_s = 0.0

        # -------------------------------------------------------------
        # Metric 4: Feather Pecking Logic (Inter-Bird Spatial Interactions)
        # -------------------------------------------------------------
        # Detect true rapid approach + close proximity overlap + victim escape burst with cooldown
        n_birds = len(current_birds)
        if n_birds >= 2:
            for i in range(n_birds):
                bird_a = current_birds[i]
                tid_a = bird_a.track_id
                if tid_a is None or tid_a not in self.bird_states:
                    continue
                state_a = self.bird_states[tid_a]

                # Condition A: Aggressor has high instantaneous velocity (single-frame spike)
                if state_a.instant_velocity_px_s >= self.cfg.feather_pecking_approach_speed_px_s:
                    for j in range(n_birds):
                        if i == j:
                            continue
                        bird_b = current_birds[j]
                        tid_b = bird_b.track_id
                        if tid_b is None or tid_b not in self.bird_states:
                            continue
                        state_b = self.bird_states[tid_b]

                        # Check pair cooldown (2.5 seconds) to avoid counting consecutive video frames as separate events
                        pair_key = (min(tid_a, tid_b), max(tid_a, tid_b))
                        last_peck_time = self.pecking_cooldowns.get(pair_key, -999.0)
                        if self.current_chunk_elapsed_s - last_peck_time < 2.5:
                            continue

                        # Condition B: Inter-bird proximity is very close (bounding box near-overlap)
                        dist = self._calc_euclidean(bird_a.centroid, bird_b.centroid)
                        if dist <= self.cfg.feather_pecking_proximity_px:
                            # Condition C: Victim has instantaneous escape burst (not passive huddle)
                            if state_b.instant_velocity_px_s >= self.cfg.feather_pecking_escape_speed_px_s:
                                self.feather_pecking_events += 1
                                state_a.pecking_aggressor_count += 1
                                state_b.pecking_victim_count += 1
                                self.pecking_cooldowns[pair_key] = self.current_chunk_elapsed_s

    def compute_chunk_summary(self) -> BehaviorChunkSummary:
        """
        Synthesizes all tracking states into the 30-minute behavior metrics record.
        """
        unique_birds = len(self.bird_states)
        avg_present = sum(self.bird_frame_counts) / max(1, len(self.bird_frame_counts))

        # Overall mobility
        all_velocities = [
            s.velocity_px_s for s in self.bird_states.values() if s.velocity_px_s > 0
        ]
        flock_mobility = sum(all_velocities) / max(1, len(all_velocities))

        # 1. Reduce Eating Aggregation
        feeding_time_total = sum(s.feeding_time_s for s in self.bird_states.values())
        feeding_bouts = sum(s.feeding_bouts for s in self.bird_states.values())
        total_reduced_eating = sum(s.reduced_eating_events for s in self.bird_states.values())
        
        # Reduced eating index: ratio of low-intake/idle bouts over total bouts
        reduced_eating_index = (
            total_reduced_eating / max(1, feeding_bouts) if feeding_bouts > 0 else 0.0
        )
        reduced_eating_index = min(1.0, round(reduced_eating_index, 3))

        # 2. Drinking Aggregation
        drinking_time_total = sum(s.drinking_time_s for s in self.bird_states.values())
        drinking_bouts = sum(s.drinking_bouts for s in self.bird_states.values())
        drinker_freq = drinking_bouts / max(1, unique_birds)

        # 3. Inactivity Aggregation
        inactivity_total = sum(s.total_inactivity_s for s in self.bird_states.values())
        total_monitored_bird_time = max(1.0, self.total_frames_in_chunk * (1.0 / 15.0) * unique_birds)
        inactivity_pct = min(100.0, (inactivity_total / total_monitored_bird_time) * 100.0)
        lethargic_count = sum(1 for s in self.bird_states.values() if s.is_lethargic_flag)

        # 4. Feather Pecking Aggregation
        agitation_score = round(self.feather_pecking_events / max(1, unique_birds), 2)

        # Per-bird breakdown records
        bird_records = []
        for s in self.bird_states.values():
            bird_records.append({
                "track_id": s.track_id,
                "bird_label": s.label,
                "bird_type": s.bird_type,
                "feeding_s": round(s.feeding_time_s, 1),
                "feeding_bouts": s.feeding_bouts,
                "reduced_eating_flag": 1 if s.reduced_eating_events > 0 else 0,
                "drinking_s": round(s.drinking_time_s, 1),
                "drinking_bouts": s.drinking_bouts,
                "inactivity_s": round(s.total_inactivity_s, 1),
                "is_lethargic": 1 if s.is_lethargic_flag else 0,
                "pecking_aggressor_count": s.pecking_aggressor_count,
                "pecking_victim_count": s.pecking_victim_count,
                "avg_velocity_px_s": round(s.velocity_px_s, 2),
            })

        return BehaviorChunkSummary(
            unique_birds=unique_birds,
            avg_birds_present=round(avg_present, 1),
            flock_mobility_px_s=round(flock_mobility, 2),
            feeding_time_total_s=round(feeding_time_total, 1),
            feeding_bouts=feeding_bouts,
            reduced_eating_events=total_reduced_eating,
            reduced_eating_index=reduced_eating_index,
            drinking_time_total_s=round(drinking_time_total, 1),
            drinking_bouts=drinking_bouts,
            drinking_frequency_per_bird=round(drinker_freq, 2),
            inactivity_time_total_s=round(inactivity_total, 1),
            inactivity_pct=round(inactivity_pct, 1),
            lethargic_birds_flagged=lethargic_count,
            feather_pecking_events=self.feather_pecking_events,
            agitation_score=agitation_score,
            bird_records=bird_records
        )
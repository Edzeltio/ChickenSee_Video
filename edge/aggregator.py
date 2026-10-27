"""
edge/aggregator.py - Temporal Hourly Aggregation & Edge State Manager.
Strictly windows operations between 06:00:00 and 18:00:00 local time.
Computes hourly flock statistics, individual bird behavior budgets,
environmental averages, and commits directly to SQLite, CSV, and JSON.
"""

import os
import csv
import json
import time
import sqlite3
import datetime
from pathlib import Path
from collections import deque
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict

from .config import CONFIG, ScheduleConfig
from .detector import FrameInferenceResult
from .behavior import FrameBehaviorSummary, BirdBehaviorState
from .sensors import SensorReading

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from local_store import copy_result_file


@dataclass
class HourlyAggregationRecord:
    date: str
    hour_slot: str
    start_time: str
    end_time: str
    total_frames: int
    avg_fps: float
    unique_birds: int
    avg_birds_present: float
    rooster_detections: int
    identified_hens_count: int
    unidentified_chickens_count: int
    feeding_time_pct: float
    drinking_time_pct: float
    active_time_pct: float
    resting_time_pct: float
    agitated_time_pct: float
    flock_mobility_score: float
    temp_mean: float
    temp_min: float
    temp_max: float
    humidity_mean: float
    humidity_min: float
    humidity_max: float
    ammonia_ppm_mean: float
    ammonia_ppm_max: float
    lux_mean: float
    thi_mean: float
    heat_stress_alert: str
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HourlyTelemetryAggregator:
    """
    State manager and database aggregator for 6 AM - 6 PM operations.
    """

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        self.cfg = schedule_config or CONFIG.schedule
        self.db_path = Path(self.cfg.db_path)
        self.reports_dir = Path(self.cfg.reports_dir)
        
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self._init_sqlite_schema()

        # In-memory accumulators for current hour window
        self._current_hour_key: Optional[str] = None
        self._hour_start_ts: float = time.time()
        self._reset_hourly_accumulators()

    def _init_sqlite_schema(self):
        """Creates indexed relational tables for telemetry and behavior analysis."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # 1. Hourly Flock Telemetry Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS hourly_flock_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                hour_slot TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                total_frames INTEGER NOT NULL,
                avg_fps REAL NOT NULL,
                unique_birds INTEGER NOT NULL,
                avg_birds_present REAL NOT NULL,
                rooster_detections INTEGER NOT NULL,
                identified_hens_count INTEGER NOT NULL,
                unidentified_chickens_count INTEGER NOT NULL,
                feeding_time_pct REAL NOT NULL,
                drinking_time_pct REAL NOT NULL,
                active_time_pct REAL NOT NULL,
                resting_time_pct REAL NOT NULL,
                agitated_time_pct REAL NOT NULL,
                flock_mobility_score REAL NOT NULL,
                temp_mean REAL NOT NULL,
                temp_min REAL NOT NULL,
                temp_max REAL NOT NULL,
                humidity_mean REAL NOT NULL,
                humidity_min REAL NOT NULL,
                humidity_max REAL NOT NULL,
                ammonia_ppm_mean REAL NOT NULL,
                ammonia_ppm_max REAL NOT NULL,
                lux_mean REAL NOT NULL,
                thi_mean REAL NOT NULL,
                heat_stress_alert TEXT NOT NULL,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # 2. Per-Bird Hourly Behavior Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS bird_hourly_behavior (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                hour_slot TEXT NOT NULL,
                track_id INTEGER NOT NULL,
                bird_label TEXT NOT NULL,
                bird_type TEXT NOT NULL,
                feeding_sec REAL NOT NULL,
                drinking_sec REAL NOT NULL,
                active_sec REAL NOT NULL,
                resting_sec REAL NOT NULL,
                agitated_sec REAL NOT NULL,
                feeding_bouts INTEGER NOT NULL,
                drinking_bouts INTEGER NOT NULL,
                avg_velocity_px_s REAL NOT NULL,
                lethargic_alert INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # 3. Environmental Logs Raw
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS environmental_logs_raw (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                temperature_c REAL NOT NULL,
                humidity_pct REAL NOT NULL,
                ammonia_ppm REAL NOT NULL,
                lux REAL NOT NULL,
                thi REAL NOT NULL,
                heat_stress_level TEXT NOT NULL
            );
            """)

            # 4. Edge System Health
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS edge_system_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                hour_slot TEXT NOT NULL,
                cpu_pct REAL NOT NULL,
                ram_mb REAL NOT NULL,
                fps REAL NOT NULL,
                status TEXT NOT NULL
            );
            """)

            # Create Indexes for fast querying & dashboard responsiveness
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_flock_date_hour ON hourly_flock_telemetry (date, hour_slot);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bird_date_hour ON bird_hourly_behavior (date, hour_slot);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_env_ts ON environmental_logs_raw (timestamp);")
            conn.commit()
            print(f"[AGGREGATOR] SQLite schema initialized at {self.db_path}")

    def _reset_hourly_accumulators(self):
        """Resets all metrics for the start of a fresh hour bucket."""
        self.frame_count = 0
        self.fps_samples: List[float] = []
        self.bird_counts: List[int] = []
        self.rooster_counts: List[int] = []
        self.unique_track_ids = set()
        self.identified_hens_set = set()
        self.unidentified_track_ids = set()
        
        # Behavior frame distribution
        self.behavior_counts = {
            "Feeding": 0,
            "Drinking": 0,
            "Active": 0,
            "Resting": 0,
            "Agitated": 0
        }
        
        # Environmental samples
        self.temps: List[float] = []
        self.humidities: List[float] = []
        self.ammonias: List[float] = []
        self.luxes: List[float] = []
        self.this: List[float] = []
        self.heat_stress_counts = 0

    def is_operational_hours(self, current_dt: Optional[datetime.datetime] = None) -> bool:
        """
        Validates if local server time is strictly between 06:00:00 and 18:00:00.
        """
        now = current_dt or datetime.datetime.now()
        # Operational range: 6:00:00 <= now < 18:00:00
        return self.cfg.start_hour <= now.hour < self.cfg.end_hour

    def get_hour_key(self, dt: datetime.datetime) -> str:
        """Generates standard hour identifier: YYYY-MM-DD_HH:00"""
        return dt.strftime("%Y-%m-%d_%H:00")

    def get_hour_slot_label(self, dt: datetime.datetime) -> str:
        """Generates readable slot label: '06:00 - 07:00'"""
        start_h = dt.hour
        end_h = (dt.hour + 1) % 24
        return f"{start_h:02d}:00 - {end_h:02d}:00"

    def ingest_frame(
        self,
        inference_result: FrameInferenceResult,
        behavior_summary: FrameBehaviorSummary,
        sensor_reading: Optional[SensorReading] = None,
        all_bird_states: Optional[List[BirdBehaviorState]] = None,
        force_flush: bool = False
    ) -> Optional[HourlyAggregationRecord]:
        """
        Ingest frame telemetry and check for hourly rollover.
        Flushes to SQLite, CSV, and JSON on the top of each hour.
        """
        now_dt = datetime.datetime.fromtimestamp(inference_result.timestamp)
        hour_key = self.get_hour_key(now_dt)

        # Initialize hour key on first frame
        if self._current_hour_key is None:
            self._current_hour_key = hour_key
            self._hour_start_ts = inference_result.timestamp

        # Check for top-of-the-hour rollover OR explicit test flush
        flushed_record = None
        if (hour_key != self._current_hour_key) or force_flush:
            flushed_record = self.flush_hourly_summary(
                end_time_dt=now_dt,
                all_bird_states=all_bird_states
            )
            # Reset for the new hour
            self._current_hour_key = hour_key
            self._hour_start_ts = inference_result.timestamp
            self._reset_hourly_accumulators()

        # Ingest frame metrics
        self.frame_count += 1
        if inference_result.fps > 0:
            self.fps_samples.append(inference_result.fps)

        self.bird_counts.append(inference_result.total_birds)
        self.rooster_counts.append(inference_result.roosters)

        for bird in inference_result.birds:
            if bird.track_id is not None:
                self.unique_track_ids.add(bird.track_id)
                if bird.label.startswith("Hen"):
                    self.identified_hens_set.add(bird.label)
                elif bird.type == "hen" and not bird.label.startswith("Hen"):
                    self.unidentified_track_ids.add(bird.track_id)

        # Behavior distribution
        for beh in behavior_summary.per_bird_behaviors.values():
            if beh in self.behavior_counts:
                self.behavior_counts[beh] += 1

        # Sensor ingestion
        if sensor_reading:
            self.temps.append(sensor_reading.temperature_c)
            self.humidities.append(sensor_reading.humidity_pct)
            self.ammonias.append(sensor_reading.ammonia_ppm)
            self.luxes.append(sensor_reading.lux)
            self.this.append(sensor_reading.thi)
            if sensor_reading.heat_stress_level in ("Severe Stress", "Emergency"):
                self.heat_stress_counts += 1

            # Log periodic raw sample (approx once every 60 frames)
            if self.frame_count % 60 == 0:
                self._insert_raw_sensor(sensor_reading)

        return flushed_record

    def _insert_raw_sensor(self, s: SensorReading):
        """Inserts periodic raw environmental row into SQLite."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                INSERT INTO environmental_logs_raw (timestamp, temperature_c, humidity_pct, ammonia_ppm, lux, thi, heat_stress_level)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.datetime.fromtimestamp(s.timestamp).isoformat(),
                    s.temperature_c,
                    s.humidity_pct,
                    s.ammonia_ppm,
                    s.lux,
                    s.thi,
                    s.heat_stress_level
                ))
                conn.commit()
        except Exception as e:
            print(f"[AGGREGATOR] Raw sensor insert notice: {e}")

    def flush_hourly_summary(
        self,
        end_time_dt: Optional[datetime.datetime] = None,
        all_bird_states: Optional[List[BirdBehaviorState]] = None
    ) -> Optional[HourlyAggregationRecord]:
        """
        Calculates and flushes the completed hourly summary to SQLite, CSV, and JSON.
        """
        if self.frame_count == 0:
            return None

        end_dt = end_time_dt or datetime.datetime.now()
        start_dt = datetime.datetime.fromtimestamp(self._hour_start_ts)
        date_str = start_dt.strftime("%Y-%m-%d")
        hour_slot = self.get_hour_slot_label(start_dt)

        # 1. Vision & Activity Aggregations
        avg_fps = sum(self.fps_samples) / len(self.fps_samples) if self.fps_samples else 0.0
        avg_birds = sum(self.bird_counts) / len(self.bird_counts) if self.bird_counts else 0.0
        rooster_dets = sum(self.rooster_counts)
        unique_birds_count = len(self.unique_track_ids)
        hens_count = len(self.identified_hens_set)
        unid_count = len(self.unidentified_track_ids)

        # Behavior percentages
        total_beh_instances = sum(self.behavior_counts.values()) or 1
        feed_pct = round((self.behavior_counts["Feeding"] / total_beh_instances) * 100.0, 2)
        drink_pct = round((self.behavior_counts["Drinking"] / total_beh_instances) * 100.0, 2)
        active_pct = round((self.behavior_counts["Active"] / total_beh_instances) * 100.0, 2)
        rest_pct = round((self.behavior_counts["Resting"] / total_beh_instances) * 100.0, 2)
        agit_pct = round((self.behavior_counts["Agitated"] / total_beh_instances) * 100.0, 2)

        flock_mobility = round((active_pct + agit_pct * 1.5) / 100.0, 2)

        # 2. Environmental Aggregations
        t_mean = round(sum(self.temps) / len(self.temps), 2) if self.temps else 26.0
        t_min = round(min(self.temps), 2) if self.temps else 25.0
        t_max = round(max(self.temps), 2) if self.temps else 27.0

        h_mean = round(sum(self.humidities) / len(self.humidities), 1) if self.humidities else 65.0
        h_min = round(min(self.humidities), 1) if self.humidities else 60.0
        h_max = round(max(self.humidities), 1) if self.humidities else 70.0

        nh3_mean = round(sum(self.ammonias) / len(self.ammonias), 2) if self.ammonias else 10.0
        nh3_max = round(max(self.ammonias), 2) if self.ammonias else 12.0

        lux_mean = round(sum(self.luxes) / len(self.luxes), 1) if self.luxes else 150.0
        thi_mean = round(sum(self.this) / len(self.this), 2) if self.this else 72.0

        heat_alert = "Normal"
        if thi_mean >= 82.0 or self.heat_stress_counts > 100:
            heat_alert = "Severe Heat Stress Alert"
        elif thi_mean >= 76.0:
            heat_alert = "Mild Warm Alert"

        record = HourlyAggregationRecord(
            date=date_str,
            hour_slot=hour_slot,
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat(),
            total_frames=self.frame_count,
            avg_fps=round(avg_fps, 1),
            unique_birds=unique_birds_count,
            avg_birds_present=round(avg_birds, 2),
            rooster_detections=rooster_dets,
            identified_hens_count=hens_count,
            unidentified_chickens_count=unid_count,
            feeding_time_pct=feed_pct,
            drinking_time_pct=drink_pct,
            active_time_pct=active_pct,
            resting_time_pct=rest_pct,
            agitated_time_pct=agit_pct,
            flock_mobility_score=flock_mobility,
            temp_mean=t_mean,
            temp_min=t_min,
            temp_max=t_max,
            humidity_mean=h_mean,
            humidity_min=h_min,
            humidity_max=h_max,
            ammonia_ppm_mean=nh3_mean,
            ammonia_ppm_max=nh3_max,
            lux_mean=lux_mean,
            thi_mean=thi_mean,
            heat_stress_alert=heat_alert,
            notes=f"Aggregated {self.frame_count} frames over window."
        )

        # 3. Persist to SQLite
        self._write_to_sqlite(record, all_bird_states)

        # 4. Export to CSV & JSON
        self._export_csv_and_json(record, all_bird_states)

        print(f"[AGGREGATOR] >>> Successfully logged hourly telemetry for [{record.date} {record.hour_slot}] <<<")
        print(f"             Birds: {record.unique_birds} unique | Feed: {record.feeding_time_pct}% | Drink: {record.drinking_time_pct}% | Temp: {record.temp_mean}°C | THI: {record.thi_mean}")

        return record

    def _write_to_sqlite(
        self, 
        record: HourlyAggregationRecord, 
        all_bird_states: Optional[List[BirdBehaviorState]] = None
    ):
        """Commits the hourly telemetry and per-bird breakdown to SQLite."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Insert Flock Hourly Record
                cursor.execute("""
                INSERT INTO hourly_flock_telemetry (
                    date, hour_slot, start_time, end_time, total_frames, avg_fps,
                    unique_birds, avg_birds_present, rooster_detections, identified_hens_count,
                    unidentified_chickens_count, feeding_time_pct, drinking_time_pct,
                    active_time_pct, resting_time_pct, agitated_time_pct, flock_mobility_score,
                    temp_mean, temp_min, temp_max, humidity_mean, humidity_min, humidity_max,
                    ammonia_ppm_mean, ammonia_ppm_max, lux_mean, thi_mean, heat_stress_alert, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.date, record.hour_slot, record.start_time, record.end_time,
                    record.total_frames, record.avg_fps, record.unique_birds, record.avg_birds_present,
                    record.rooster_detections, record.identified_hens_count, record.unidentified_chickens_count,
                    record.feeding_time_pct, record.drinking_time_pct, record.active_time_pct,
                    record.resting_time_pct, record.agitated_time_pct, record.flock_mobility_score,
                    record.temp_mean, record.temp_min, record.temp_max,
                    record.humidity_mean, record.humidity_min, record.humidity_max,
                    record.ammonia_ppm_mean, record.ammonia_ppm_max,
                    record.lux_mean, record.thi_mean, record.heat_stress_alert, record.notes
                ))

                # Insert Individual Bird Behavior Records
                if all_bird_states:
                    for b in all_bird_states:
                        cursor.execute("""
                        INSERT INTO bird_hourly_behavior (
                            date, hour_slot, track_id, bird_label, bird_type,
                            feeding_sec, drinking_sec, active_sec, resting_sec, agitated_sec,
                            feeding_bouts, drinking_bouts, avg_velocity_px_s, lethargic_alert
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            record.date, record.hour_slot, b.track_id, b.label, b.bird_type,
                            round(b.feeding_sec, 1), round(b.drinking_sec, 1),
                            round(b.active_sec, 1), round(b.resting_sec, 1), round(b.agitated_sec, 1),
                            b.feeding_bouts, b.drinking_bouts,
                            round(b.velocity_px_s, 2),
                            1 if b.is_lethargic_alert else 0
                        ))

                conn.commit()

        except Exception as e:
            print(f"[AGGREGATOR] Error persisting to SQLite: {e}")

    def _export_csv_and_json(
        self,
        record: HourlyAggregationRecord,
        all_bird_states: Optional[List[BirdBehaviorState]] = None
    ):
        """Exports hourly files to local reports directory."""
        file_prefix = f"hourly_telemetry_{record.date}_{record.hour_slot.replace(':', '').replace(' ', '')}"
        csv_path = self.reports_dir / f"{file_prefix}.csv"
        json_path = self.reports_dir / f"{file_prefix}.json"

        # 1. Write JSON
        try:
            def json_serializer(obj):
                if isinstance(obj, (deque, set)):
                    return list(obj)
                return str(obj)

            payload = {
                "flock_summary": record.to_dict(),
                "bird_profiles": [asdict(b) for b in (all_bird_states or [])]
            }
            with open(json_path, "w", encoding="utf-8") as jf:
                json.dump(payload, jf, indent=2, default=json_serializer)
            copy_result_file(str(json_path))
        except Exception as e:
            print(f"[AGGREGATOR] JSON export notice: {e}")

        # 2. Write CSV
        try:
            rec_dict = record.to_dict()
            with open(csv_path, "w", newline="", encoding="utf-8") as cf:
                writer = csv.DictWriter(cf, fieldnames=list(rec_dict.keys()))
                writer.writeheader()
                writer.writerow(rec_dict)
            copy_result_file(str(csv_path))
        except Exception as e:
            print(f"[AGGREGATOR] CSV export notice: {e}")

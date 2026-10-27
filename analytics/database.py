"""
analytics/database.py
SQLAlchemy schema and job state manager for the ZAMPEN Behavior & Sensor Analytics Pipeline.
Prevents duplicate video analysis and stores interval metrics.
"""

import datetime
import hashlib
from pathlib import Path
from typing import List, Optional, Dict, Any
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Float, DateTime, Boolean,
    ForeignKey, Text, Index, Enum as SQLEnum, select, func, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
import enum

from .config import CONFIG

Base = declarative_base()


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProcessingJob(Base):
    """Tracks video analysis jobs to guarantee idempotency and prevent duplicate processing."""
    __tablename__ = "processing_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_filename = Column(String(255), unique=True, nullable=False, index=True)
    video_path = Column(String(512), nullable=False)
    date_slot = Column(String(32), nullable=False, index=True)      # YYYY-MM-DD
    hour_slot = Column(String(32), nullable=False, index=True)      # HH:00
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    
    status = Column(SQLEnum(JobStatus), default=JobStatus.QUEUED, nullable=False, index=True)
    frames_processed = Column(Integer, default=0)
    progress_frames = Column(Integer, nullable=True)
    total_frames = Column(Integer, default=0)
    source_size = Column(BigInteger, nullable=True)
    source_mtime_ns = Column(BigInteger, nullable=True)
    source_ctime_ns = Column(BigInteger, nullable=True)
    processing_time_s = Column(Float, default=0.0)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    half_hour_metrics = relationship("HalfHourMetrics", back_populates="job", cascade="all, delete-orphan")


class HalfHourMetrics(Base):
    """
    Stores time-chunked sensor and vision behavior metrics.
    Each video produces one or more records based on its duration and configured chunk length.
    """
    __tablename__ = "half_hour_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("processing_jobs.id"), nullable=False, index=True)
    
    date_slot = Column(String(32), nullable=False, index=True)       # YYYY-MM-DD
    hour_slot = Column(String(32), nullable=False, index=True)       # HH:00
    chunk_index = Column(Integer, nullable=False)                    # 1 (00-30 min) or 2 (30-60 min)
    chunk_start = Column(DateTime, nullable=False, index=True)
    chunk_end = Column(DateTime, nullable=False, index=True)

    # Environmental Sensor 30-minute Averages
    temp_avg_c = Column(Float, nullable=False, default=0.0)
    temp_min_c = Column(Float, nullable=False, default=0.0)
    temp_max_c = Column(Float, nullable=False, default=0.0)
    humidity_avg_pct = Column(Float, nullable=False, default=0.0)
    humidity_min_pct = Column(Float, nullable=False, default=0.0)
    humidity_max_pct = Column(Float, nullable=False, default=0.0)
    ammonia_ppm_avg = Column(Float, nullable=False, default=0.0)
    ammonia_ppm_max = Column(Float, nullable=False, default=0.0)
    lux_avg = Column(Float, nullable=False, default=0.0)
    thi_avg = Column(Float, nullable=False, default=0.0)
    heat_stress_level = Column(String(32), nullable=False, default="Normal")
    sensor_data_available = Column(Boolean, nullable=False, default=False)

    # Flock Vision & ByteTrack Metrics
    unique_birds_tracked = Column(Integer, nullable=False, default=0)
    avg_birds_in_frame = Column(Float, nullable=False, default=0.0)
    flock_mobility_px_s = Column(Float, nullable=False, default=0.0)

    # Specific Behavior Metrics
    # 1. Reduce Eating Metric
    feeding_time_total_s = Column(Float, nullable=False, default=0.0)
    feeding_bouts = Column(Integer, nullable=False, default=0)
    reduced_eating_events = Column(Integer, nullable=False, default=0)  # Low-motion near feeder
    reduced_eating_index = Column(Float, nullable=False, default=0.0)   # % of feeder visits with low intake motion

    # 2. Drinking Metric
    drinking_time_total_s = Column(Float, nullable=False, default=0.0)
    drinking_bouts = Column(Integer, nullable=False, default=0)
    drinking_frequency_per_bird = Column(Float, nullable=False, default=0.0)

    # 3. Inactivity Metric
    inactivity_time_total_s = Column(Float, nullable=False, default=0.0)
    inactivity_pct = Column(Float, nullable=False, default=0.0)
    lethargic_birds_flagged = Column(Integer, nullable=False, default=0)

    # 4. Feather Pecking Metric
    feather_pecking_events = Column(Integer, nullable=False, default=0)
    agitation_score = Column(Float, nullable=False, default=0.0)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    job = relationship("ProcessingJob", back_populates="half_hour_metrics")
    bird_metrics = relationship("BirdTrackMetrics", back_populates="chunk_metric", cascade="all, delete-orphan")


class BirdTrackMetrics(Base):
    """Detailed per-track behavior metrics for individual birds within each 30-min window."""
    __tablename__ = "bird_track_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_metric_id = Column(Integer, ForeignKey("half_hour_metrics.id"), nullable=False, index=True)
    
    track_id = Column(Integer, nullable=False, index=True)
    bird_label = Column(String(64), nullable=False)       # e.g., "Hen1 (Green)", "Rooster", "Chicken"
    bird_type = Column(String(32), nullable=False)        # "hen" or "rooster"
    
    feeding_s = Column(Float, default=0.0)
    feeding_bouts = Column(Integer, default=0)
    reduced_eating_flag = Column(Integer, default=0)       # 1 if individual flagged for reduced intake

    drinking_s = Column(Float, default=0.0)
    drinking_bouts = Column(Integer, default=0)

    inactivity_s = Column(Float, default=0.0)
    is_lethargic = Column(Integer, default=0)

    pecking_aggressor_count = Column(Integer, default=0)
    pecking_victim_count = Column(Integer, default=0)
    avg_velocity_px_s = Column(Float, default=0.0)

    chunk_metric = relationship("HalfHourMetrics", back_populates="bird_metrics")


# Indexes for rapid dashboard time-series queries
Index("idx_half_hour_date_hour", HalfHourMetrics.date_slot, HalfHourMetrics.hour_slot)
Index("idx_half_hour_range", HalfHourMetrics.chunk_start, HalfHourMetrics.chunk_end)


class DatabaseManager:
    """Helper manager for connection pooling, job leasing, and metrics retrieval."""

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or CONFIG.analytics_db_url
        self.engine = create_engine(
            self.db_url,
            connect_args={"check_same_thread": False} if self.db_url.startswith("sqlite") else {}
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.init_db()

    def init_db(self):
        """Creates tables if they do not exist."""
        Base.metadata.create_all(bind=self.engine)
        columns = {column["name"] for column in inspect(self.engine).get_columns("processing_jobs")}
        migrations = {
            "progress_frames": "INTEGER",
            "source_size": "BIGINT",
            "source_mtime_ns": "BIGINT",
            "source_ctime_ns": "BIGINT",
        }
        with self.engine.begin() as connection:
            for name, sql_type in migrations.items():
                if name not in columns:
                    connection.execute(text(
                        f"ALTER TABLE processing_jobs ADD COLUMN {name} {sql_type}"
                    ))

            metric_columns = {
                column["name"]
                for column in inspect(self.engine).get_columns("half_hour_metrics")
            }
            if "sensor_data_available" not in metric_columns:
                connection.execute(text(
                    "ALTER TABLE half_hour_metrics "
                    "ADD COLUMN sensor_data_available BOOLEAN NOT NULL DEFAULT 0"
                ))
                connection.execute(text("""
                    UPDATE half_hour_metrics
                    SET sensor_data_available = CASE
                        WHEN lower(heat_stress_level) != 'unavailable'
                          OR temp_avg_c != 0 OR temp_min_c != 0 OR temp_max_c != 0
                          OR humidity_avg_pct != 0 OR humidity_min_pct != 0 OR humidity_max_pct != 0
                          OR ammonia_ppm_avg != 0 OR ammonia_ppm_max != 0
                          OR lux_avg != 0 OR thi_avg != 0
                        THEN 1 ELSE 0
                    END
                """))

    def get_session(self) -> Session:
        return self.SessionLocal()

    @staticmethod
    def file_signature(filepath: str) -> tuple[int, int, int]:
        stat = Path(filepath).stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    @staticmethod
    def file_version_matches(
        job: ProcessingJob, signature: tuple[int, int, int]
    ) -> bool:
        if job.source_size is not None:
            return (
                job.source_size,
                job.source_mtime_ns,
                job.source_ctime_ns,
            ) == signature
        if job.completed_at is None:
            return True
        completed_utc = job.completed_at.replace(tzinfo=datetime.timezone.utc)
        completed_ns = int(completed_utc.timestamp() * 1_000_000_000)
        return signature[2] <= completed_ns

    def is_file_processed(
        self,
        filename: str,
        filepath: Optional[str] = None,
        signature: Optional[tuple[int, int, int]] = None,
    ) -> bool:
        """Checks the completed job for this path and file version."""
        with self.get_session() as session:
            if filepath is None:
                job = session.query(ProcessingJob).filter(
                    ProcessingJob.video_filename == filename,
                    ProcessingJob.status.in_((JobStatus.COMPLETED, JobStatus.CANCELLED)),
                ).first()
            else:
                job = session.query(ProcessingJob).filter(
                    ProcessingJob.video_path == filepath,
                    ProcessingJob.status.in_((JobStatus.COMPLETED, JobStatus.CANCELLED)),
                ).first()
                if job is None and filepath == filename:
                    job = session.query(ProcessingJob).filter(
                        ProcessingJob.video_path == filename,
                        ProcessingJob.status.in_((JobStatus.COMPLETED, JobStatus.CANCELLED)),
                    ).first()
            if job is None:
                return False
            return signature is None or self.file_version_matches(job, signature)

    def has_failed_file_version(
        self, filepath: str, signature: tuple[int, int, int]
    ) -> bool:
        """Checks whether this exact file version has already failed analysis."""
        with self.get_session() as session:
            job = session.query(ProcessingJob).filter(
                ProcessingJob.video_path == filepath,
                ProcessingJob.status == JobStatus.FAILED,
            ).first()
            return bool(
                job
                and job.source_size is not None
                and self.file_version_matches(job, signature)
            )

    def lease_job(
        self,
        filename: str,
        filepath: str,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime,
        signature: Optional[tuple[int, int, int]] = None,
    ) -> Optional[ProcessingJob]:
        """Atomically leases or creates a job in PROCESSING status."""
        with self.get_session() as session:
            job = session.query(ProcessingJob).filter_by(video_path=filepath).first()
            if not job:
                job = session.query(ProcessingJob).filter_by(video_filename=filename).first()
                if job and job.video_path != filepath:
                    suffix = hashlib.sha256(filepath.encode("utf-8")).hexdigest()[:12]
                    path_obj = Path(filename)
                    filename = f"{path_obj.stem}_{suffix}{path_obj.suffix}"
                    job = session.query(ProcessingJob).filter_by(video_filename=filename).first()
            if job:
                if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
                    if signature is None or self.file_version_matches(job, signature):
                        return None
                    for metric in job.half_hour_metrics:
                        session.delete(metric)
                    job.date_slot = start_dt.strftime("%Y-%m-%d")
                    job.hour_slot = start_dt.strftime("%H:00")
                    job.start_time = start_dt
                    job.end_time = end_dt
                    job.frames_processed = 0
                    job.processing_time_s = 0.0
                    job.completed_at = None
                job.status = JobStatus.PROCESSING
                job.error_message = None
                job.progress_frames = 0
                job.total_frames = 0
                if signature is not None:
                    job.source_size, job.source_mtime_ns, job.source_ctime_ns = signature
            else:
                job = ProcessingJob(
                    video_filename=filename,
                    video_path=filepath,
                    date_slot=start_dt.strftime("%Y-%m-%d"),
                    hour_slot=start_dt.strftime("%H:00"),
                    start_time=start_dt,
                    end_time=end_dt,
                    status=JobStatus.PROCESSING,
                    source_size=signature[0] if signature else None,
                    source_mtime_ns=signature[1] if signature else None,
                    source_ctime_ns=signature[2] if signature else None,
                )
                session.add(job)
            session.commit()
            session.refresh(job)
            return job

    def mark_job_completed(self, job_id: int, frames: int, total_frames: int, duration_s: float):
        with self.get_session() as session:
            job = session.get(ProcessingJob, job_id)
            if job and job.status == JobStatus.PROCESSING:
                job.status = JobStatus.COMPLETED
                job.frames_processed = frames
                job.progress_frames = total_frames
                job.total_frames = total_frames
                job.processing_time_s = duration_s
                job.completed_at = datetime.datetime.utcnow()
                session.commit()

    def cancel_processing_job(self, job_id: int) -> bool:
        """Mark an active analysis as cancelled so its worker can stop safely."""
        with self.get_session() as session:
            job = session.get(ProcessingJob, job_id)
            if not job or job.status != JobStatus.PROCESSING:
                return False
            job.status = JobStatus.CANCELLED
            job.error_message = "Cancelled by user."
            session.commit()
            return True

    def is_job_cancelled(self, job_id: int) -> bool:
        with self.get_session() as session:
            job = session.get(ProcessingJob, job_id)
            return job is not None and job.status == JobStatus.CANCELLED

    def update_job_progress(self, job_id: int, progress_frames: int, total_frames: int):
        with self.get_session() as session:
            job = session.get(ProcessingJob, job_id)
            if job and job.status == JobStatus.PROCESSING:
                job.progress_frames = max(0, min(progress_frames, total_frames))
                job.total_frames = max(0, total_frames)
                session.commit()

    def mark_job_failed(self, job_id: int, error_msg: str):
        with self.get_session() as session:
            job = session.get(ProcessingJob, job_id)
            if job and job.status == JobStatus.PROCESSING:
                job.status = JobStatus.FAILED
                job.error_message = str(error_msg)
                session.commit()

    def save_30m_metrics(self, job_id: int, metrics_list: List[Dict[str, Any]], bird_records_list: List[List[Dict[str, Any]]]):
        """Persists interval metric records and associated individual bird profiles."""
        with self.get_session() as session:
            for m_dict, birds in zip(metrics_list, bird_records_list):
                database_metrics = dict(m_dict)
                sensor_data_available = database_metrics.pop("sensor_data_available", None)
                if sensor_data_available is None:
                    sensor_data_available = any(
                        database_metrics.get(field) is not None
                        for field in (
                            "temp_avg_c", "temp_min_c", "temp_max_c",
                            "humidity_avg_pct", "humidity_min_pct", "humidity_max_pct",
                            "ammonia_ppm_avg", "ammonia_ppm_max", "lux_avg", "thi_avg",
                        )
                    )
                database_metrics["sensor_data_available"] = bool(sensor_data_available)
                for field in (
                    "temp_avg_c", "temp_min_c", "temp_max_c",
                    "humidity_avg_pct", "humidity_min_pct", "humidity_max_pct",
                    "ammonia_ppm_avg", "ammonia_ppm_max", "lux_avg", "thi_avg",
                ):
                    if database_metrics.get(field) is None:
                        database_metrics[field] = 0.0
                if database_metrics.get("heat_stress_level") is None:
                    database_metrics["heat_stress_level"] = "Unavailable"
                metric = HalfHourMetrics(job_id=job_id, **database_metrics)
                session.add(metric)
                session.flush()

                for b_dict in birds:
                    b_rec = BirdTrackMetrics(chunk_metric_id=metric.id, **b_dict)
                    session.add(b_rec)
            session.commit()

    def get_latest_metrics(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent video analysis and its intervals."""
        with self.get_session() as session:
            latest_job = session.query(ProcessingJob).filter_by(
                status=JobStatus.COMPLETED
            ).order_by(ProcessingJob.end_time.desc()).first()

            if not latest_job:
                return None

            chunks = session.query(HalfHourMetrics).filter_by(
                job_id=latest_job.id
            ).order_by(HalfHourMetrics.chunk_index.asc()).all()

            return {
                "job_id": latest_job.id,
                "video_filename": latest_job.video_filename,
                "date": latest_job.date_slot,
                "hour_slot": latest_job.hour_slot,
                "processed_at": latest_job.completed_at.isoformat() if latest_job.completed_at else None,
                "chunks_30m": [
                    {
                        "chunk_index": c.chunk_index,
                        "start_time": c.chunk_start.isoformat(),
                        "end_time": c.chunk_end.isoformat(),
                        "sensors": {
                            "temp_avg_c": c.temp_avg_c if c.sensor_data_available else None,
                            "temp_min_c": c.temp_min_c if c.sensor_data_available else None,
                            "temp_max_c": c.temp_max_c if c.sensor_data_available else None,
                            "humidity_avg_pct": c.humidity_avg_pct if c.sensor_data_available else None,
                            "ammonia_ppm_avg": c.ammonia_ppm_avg if c.sensor_data_available else None,
                            "lux_avg": c.lux_avg if c.sensor_data_available else None,
                            "thi_avg": c.thi_avg if c.sensor_data_available else None,
                            "heat_stress_level": (
                                c.heat_stress_level if c.sensor_data_available else "Unavailable"
                            ),
                            "sensor_data_available": c.sensor_data_available,
                        },
                        "behaviors": {
                            "unique_birds": c.unique_birds_tracked,
                            "avg_birds_present": c.avg_birds_in_frame,
                            "feeding_time_total_s": round(c.feeding_time_total_s, 1),
                            "feeding_bouts": c.feeding_bouts,
                            "reduced_eating_events": c.reduced_eating_events,
                            "reduced_eating_index_pct": round(c.reduced_eating_index * 100, 1),
                            "drinking_time_total_s": round(c.drinking_time_total_s, 1),
                            "drinking_bouts": c.drinking_bouts,
                            "inactivity_time_total_s": round(c.inactivity_time_total_s, 1),
                            "inactivity_pct": round(c.inactivity_pct, 1),
                            "lethargic_birds_flagged": c.lethargic_birds_flagged,
                            "feather_pecking_events": c.feather_pecking_events,
                            "agitation_score": round(c.agitation_score, 2),
                            "flock_mobility_px_s": round(c.flock_mobility_px_s, 2),
                        }
                    }
                    for c in chunks
                ]
            }

    def get_metrics_by_range(self, start_dt: datetime.datetime, end_dt: datetime.datetime) -> List[Dict[str, Any]]:
        """Queries historical 30-minute sensor averages and behavior analytics by timestamp range."""
        with self.get_session() as session:
            chunks = session.query(HalfHourMetrics).filter(
                HalfHourMetrics.chunk_start >= start_dt,
                HalfHourMetrics.chunk_end <= end_dt
            ).order_by(HalfHourMetrics.chunk_start.asc()).all()

            results = []
            for c in chunks:
                results.append({
                    "id": c.id,
                    "date": c.date_slot,
                    "hour_slot": c.hour_slot,
                    "chunk_index": c.chunk_index,
                    "start_time": c.chunk_start.isoformat(),
                    "end_time": c.chunk_end.isoformat(),
                    "environment": {
                        "temperature_c": c.temp_avg_c if c.sensor_data_available else None,
                        "humidity_pct": c.humidity_avg_pct if c.sensor_data_available else None,
                        "ammonia_ppm": c.ammonia_ppm_avg if c.sensor_data_available else None,
                        "lux": c.lux_avg if c.sensor_data_available else None,
                        "thi": c.thi_avg if c.sensor_data_available else None,
                        "heat_stress_level": (
                            c.heat_stress_level if c.sensor_data_available else "Unavailable"
                        ),
                        "sensor_data_available": c.sensor_data_available,
                    },
                    "behavior_analytics": {
                        "unique_birds": c.unique_birds_tracked,
                        "reduced_eating_events": c.reduced_eating_events,
                        "reduced_eating_pct": round(c.reduced_eating_index * 100, 1),
                        "drinking_time_s": round(c.drinking_time_total_s, 1),
                        "drinking_bouts": c.drinking_bouts,
                        "inactivity_pct": round(c.inactivity_pct, 1),
                        "lethargic_birds": c.lethargic_birds_flagged,
                        "feather_pecking_events": c.feather_pecking_events,
                        "flock_mobility_px_s": round(c.flock_mobility_px_s, 2)
                    }
                })
            return results


DB = DatabaseManager()

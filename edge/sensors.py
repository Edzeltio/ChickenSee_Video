"""
edge/sensors.py - Sensor Ingestion & Temporal Synchronization Hub.
Supports Serial COM/RS485 telemetry and a diurnal Farm Simulator.
Provides thread-safe sub-second timestamp pairing with computer vision frames.
"""

import math
import time
import json
import random
import threading
from collections import deque
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List

from .config import CONFIG, SensorConfig


@dataclass
class SensorReading:
    timestamp: float              # Epoch time in seconds
    temperature_c: float          # Ambient temperature in Celsius
    humidity_pct: float           # Relative Humidity in %
    ammonia_ppm: float            # NH3 concentration in ppm
    lux: float                    # Light level in lux
    thi: float                    # Temperature Humidity Index (Heat Stress)
    feeder_hopper_kg: float       # Feeder load cell reading (kg)
    water_flow_l_min: float       # Water meter reading (L/min)
    heat_stress_level: str        # "Comfort", "Mild Alert", "Severe Stress", "Emergency"
    source: str = "simulation"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_thi(temp_c: float, rh_pct: float) -> float:
    """
    Poultry Temperature-Humidity Index (THI).
    Formula: THI = 0.8 * T + (RH / 100) * (T - 14.4) + 46.4
    """
    return round(0.8 * temp_c + (rh_pct / 100.0) * (temp_c - 14.4) + 46.4, 2)


def evaluate_heat_stress(thi: float) -> str:
    """Classify thermal stress based on poultry physiology."""
    if thi < 72.0:
        return "Comfort"
    elif thi < 78.0:
        return "Mild Alert"
    elif thi < 84.0:
        return "Severe Stress"
    else:
        return "Emergency"


class SensorHub:
    """
    Thread-safe sensor ingestion hub with temporal alignment ring buffer.
    """

    def __init__(self, sensor_config: Optional[SensorConfig] = None):
        self.cfg = sensor_config or CONFIG.sensor
        self.cfg.source_type = str(self.cfg.source_type).strip().lower()
        if self.cfg.source_type not in ("serial", "simulation"):
            raise ValueError(
                f"Unsupported sensor source {self.cfg.source_type!r}; "
                "implemented sources are 'serial' and 'simulation'."
            )
        self._buffer: deque[SensorReading] = deque(maxlen=3600)  # Hold up to 1 hour of 1Hz telemetry
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._latest_reading: Optional[SensorReading] = None
        self._serial_conn = None
        self._last_error_log_at = 0.0

        # Start ingestion loop
        self.start()

    def start(self):
        """Start background sensor polling worker."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._ingestion_loop, daemon=True, name="SensorHubWorker")
        self._worker_thread.start()
        print(f"[SENSOR_HUB] Sensor ingestion worker active (Source: {self.cfg.source_type})")

    def stop(self):
        """Cleanly stop sensor polling."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        if self._serial_conn:
            try:
                self._serial_conn.close()
            except Exception:
                pass
        print("[SENSOR_HUB] Sensor ingestion stopped.")

    def _ingestion_loop(self):
        """Continuous background polling loop."""
        while not self._stop_event.is_set():
            try:
                if self.cfg.source_type == "serial":
                    reading = self._read_serial()
                else:
                    reading = self._generate_simulated_reading()

                if reading:
                    with self._lock:
                        self._buffer.append(reading)
                        self._latest_reading = reading

            except Exception as e:
                # Missing/invalid hardware telemetry must not be replaced by plausible-looking
                # simulated values in a production report.
                now = time.monotonic()
                if now - self._last_error_log_at >= 30.0:
                    print(f"[SENSOR ERROR] {e}; no synthetic sample recorded.")
                    self._last_error_log_at = now

            time.sleep(self.cfg.poll_interval_s)

    def _generate_simulated_reading(self) -> SensorReading:
        """
        Generates physiologically and meteorologically accurate poultry barn data
        reflecting diurnal cycles between 6 AM and 6 PM.
        """
        now = time.time()
        local_tm = time.localtime(now)
        # Fractional hour of the day (0.0 to 24.0)
        hour_fraction = local_tm.tm_hour + (local_tm.tm_min / 60.0) + (local_tm.tm_sec / 3600.0)

        # Diurnal temperature curve: peaks around 13:00 - 14:00 (midday)
        # 6 AM ~ 24°C, 13:30 ~ 32.5°C, 18:00 ~ 28°C
        diurnal_factor = math.sin(math.pi * max(0.0, min(1.0, (hour_fraction - 6.0) / 12.0)))
        temp = 24.0 + (9.0 * diurnal_factor) + random.uniform(-0.4, 0.4)

        # Humidity is inversely related to temperature: high at dawn (~80%), lower at midday (~55%)
        humidity = 82.0 - (26.0 * diurnal_factor) + random.uniform(-1.0, 1.0)
        humidity = max(30.0, min(95.0, humidity))

        # Solar / indoor lux: 0 before 6am, peaks midday at ~450 lux, drops at 18:00
        lux = max(15.0, 480.0 * diurnal_factor + random.uniform(-10.0, 10.0))

        # Ammonia (NH3) gradually builds up in the barn during daytime bird activity (8 - 22 ppm)
        base_nh3 = 9.0 + (10.0 * (hour_fraction - 6.0) / 12.0)
        ammonia = max(4.0, base_nh3 + random.uniform(-1.2, 1.2))

        # Feeder hopper decreases slowly over the day as chickens feed (50kg capacity)
        feed_consumed = max(0.0, min(25.0, (hour_fraction - 6.0) * 1.5))
        hopper_kg = max(5.0, 48.0 - feed_consumed + random.uniform(-0.1, 0.1))

        # Water flow fluctuates with bird drinking activity (0.1 to 1.8 L/min)
        water_flow = 0.3 + (0.9 * diurnal_factor) + random.uniform(-0.15, 0.15)
        water_flow = max(0.05, water_flow)

        thi = calculate_thi(temp, humidity)
        stress_level = evaluate_heat_stress(thi)

        return SensorReading(
            timestamp=now,
            temperature_c=round(temp, 2),
            humidity_pct=round(humidity, 1),
            ammonia_ppm=round(ammonia, 2),
            lux=round(lux, 1),
            thi=thi,
            feeder_hopper_kg=round(hopper_kg, 2),
            water_flow_l_min=round(water_flow, 2),
            heat_stress_level=stress_level,
            source="simulation"
        )

    @staticmethod
    def _parse_serial_line(line: str) -> SensorReading:
        """Parse JSON or CSV telemetry; reject incomplete or physically invalid samples."""
        text = line.strip()
        if not text:
            raise ValueError("Serial sensor sent an empty telemetry line")
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON sensor line: {error}") from error
            if not isinstance(data, dict):
                raise ValueError("JSON sensor telemetry must be an object")
            keys = ("temp", "hum", "nh3", "lux", "hopper", "water")
            missing = [key for key in keys if key not in data]
            if missing:
                raise ValueError(
                    "Serial JSON is missing required fields: " + ", ".join(missing)
                )
            values = {key: data[key] for key in keys}
        else:
            parts = [part.strip() for part in text.split(",")]
            if len(parts) != 6:
                raise ValueError(
                    "Serial CSV must contain six values in this order: "
                    "temp,hum,nh3,lux,hopper,water"
                )
            values = dict(zip(("temp", "hum", "nh3", "lux", "hopper", "water"), parts))

        try:
            temp, hum, nh3, lux, hopper, water = (
                float(values[key])
                for key in ("temp", "hum", "nh3", "lux", "hopper", "water")
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Serial sensor fields must all be numeric") from error

        numeric_values = (temp, hum, nh3, lux, hopper, water)
        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError("Serial sensor fields must be finite numbers")
        if not -20.0 <= temp <= 80.0:
            raise ValueError(f"Temperature is outside the accepted range: {temp}")
        if not 0.0 <= hum <= 100.0:
            raise ValueError(f"Relative humidity must be between 0 and 100: {hum}")
        if any(value < 0 for value in (nh3, lux, hopper, water)):
            raise ValueError("Ammonia, lux, hopper load, and water flow cannot be negative")

        thi = calculate_thi(temp, hum)
        return SensorReading(
            timestamp=time.time(),
            temperature_c=temp,
            humidity_pct=hum,
            ammonia_ppm=nh3,
            lux=lux,
            thi=thi,
            feeder_hopper_kg=hopper,
            water_flow_l_min=water,
            heat_stress_level=evaluate_heat_stress(thi),
            source="serial",
        )

    def _read_serial(self) -> Optional[SensorReading]:
        """Read one complete JSON/CSV sample from the configured USB/RS485 device."""
        import serial

        if self._serial_conn is None:
            self._serial_conn = serial.Serial(
                self.cfg.serial_port,
                self.cfg.serial_baud,
                timeout=1.0,
            )
        line = self._serial_conn.readline().decode("utf-8", errors="strict").strip()
        if not line:
            return None
        return self._parse_serial_line(line)

    def get_latest(self) -> Optional[SensorReading]:
        """Retrieve most recent sensor sample."""
        with self._lock:
            return self._latest_reading

    def get_synchronized_reading(
        self, 
        frame_timestamp: float, 
        tolerance_s: Optional[float] = None
    ) -> Optional[SensorReading]:
        """
        Temporal Synchronization: Finds the sensor sample closest to the frame's timestamp
        within the specified delta tolerance window.
        """
        max_tol = tolerance_s or self.cfg.sync_tolerance_s
        with self._lock:
            if not self._buffer:
                return self._latest_reading

            best_reading = None
            best_diff = float("inf")

            # Search backwards from newest readings
            for r in reversed(self._buffer):
                diff = abs(r.timestamp - frame_timestamp)
                if diff < best_diff:
                    best_diff = diff
                    best_reading = r
                elif diff > best_diff and diff > max_tol:
                    # Once difference begins increasing beyond tolerance, break early
                    break

            if best_diff <= max_tol:
                return best_reading

            # Return latest if within loose bounds
            return self._latest_reading

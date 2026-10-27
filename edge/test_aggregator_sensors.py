import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edge.aggregator import HourlyTelemetryAggregator
from edge.config import ScheduleConfig


class AggregatorSensorAvailabilityTests(unittest.TestCase):
    def test_missing_sensor_data_is_not_exported_as_measurements(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = ScheduleConfig()
            config.db_path = str(Path(temp_dir) / "telemetry.sqlite")
            config.reports_dir = str(Path(temp_dir) / "reports")
            aggregator = HourlyTelemetryAggregator(config)
            aggregator.frame_count = 1

            with patch("edge.aggregator.copy_result_file"):
                record = aggregator.flush_hourly_summary()

            self.assertFalse(record.sensor_data_available)
            self.assertIsNone(record.temp_mean)
            self.assertIsNone(record.thi_mean)
            self.assertEqual(record.heat_stress_alert, "Unavailable")

            with sqlite3.connect(config.db_path) as connection:
                stored = connection.execute(
                    "SELECT temp_mean, thi_mean, sensor_data_available "
                    "FROM hourly_flock_telemetry"
                ).fetchone()
            self.assertEqual(stored, (0.0, 0.0, 0))

            report_path = next(Path(config.reports_dir).glob("*.json"))
            summary = json.loads(report_path.read_text(encoding="utf-8"))["flock_summary"]
            self.assertIsNone(summary["temp_mean"])
            self.assertFalse(summary["sensor_data_available"])


if __name__ == "__main__":
    unittest.main()

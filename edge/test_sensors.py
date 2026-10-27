import unittest

from edge.config import SensorConfig
from edge.sensors import SensorHub


class SerialSensorParserTests(unittest.TestCase):
    def test_parses_complete_json_telemetry(self):
        reading = SensorHub._parse_serial_line(
            '{"temp":28.5,"hum":68.2,"nh3":12.4,"lux":350,"hopper":40,"water":0.5}'
        )

        self.assertEqual(reading.source, "serial")
        self.assertEqual(reading.temperature_c, 28.5)
        self.assertEqual(reading.feeder_hopper_kg, 40)
        self.assertGreater(reading.thi, 0)

    def test_parses_complete_csv_telemetry(self):
        reading = SensorHub._parse_serial_line("28.5,68.2,12.4,350,40,0.5")

        self.assertEqual(reading.humidity_pct, 68.2)
        self.assertEqual(reading.water_flow_l_min, 0.5)

    def test_rejects_missing_fields_and_wrong_csv_width(self):
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            SensorHub._parse_serial_line('{"temp":28,"hum":65}')
        with self.assertRaisesRegex(ValueError, "six values"):
            SensorHub._parse_serial_line("28,65,10,350")

    def test_rejects_nonfinite_or_out_of_range_values(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            SensorHub._parse_serial_line("nan,65,10,350,40,0.5")
        with self.assertRaisesRegex(ValueError, "humidity"):
            SensorHub._parse_serial_line("28,105,10,350,40,0.5")

    def test_unknown_sensor_sources_fail_instead_of_simulating(self):
        with self.assertRaisesRegex(ValueError, "implemented sources"):
            SensorHub(SensorConfig(source_type="mqtt"))


if __name__ == "__main__":
    unittest.main()

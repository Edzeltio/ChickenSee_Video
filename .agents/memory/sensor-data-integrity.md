---
name: Sensor data integrity
description: Keep real poultry-farm sensor readings distinct from simulated or unavailable values.
---

The edge pipeline supports the `simulation` and `serial` sensor sources. Serial input must contain a complete, valid sample; a serial failure or missing sample must not be replaced with plausible simulated readings. Hourly reports should represent missing environmental measurements as unavailable and expose an availability flag to legacy SQLite consumers.

**Why:** Fabricated environmental values can hide a disconnected Arduino or create false heat-stress and farm-condition reports.

**How to apply:** Add new sensor adapters as explicit source types with validated payloads. Preserve simulation as a deliberate development mode, and ensure exports and database records distinguish absent hardware data from measured values.

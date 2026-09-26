"""Deterministic building-level telemetry for the software demo simulation."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from backend.core.events import EventTrace
from backend.core.time import utc_now


class BuildingEnergyTelemetry:
    """Rolling building meter estimate derived from current simulated zone states."""

    def __init__(self, base_load_kw: float = 2.0, max_samples: int = 120,
                 expected_zone_updates: int = 4):
        self.base_load_kw = float(base_load_kw)
        self.samples: deque[dict] = deque(maxlen=max_samples)
        self.energy_kwh = 0.0
        self.expected_zone_updates = max(1, expected_zone_updates)

    def record(self, zone_states: dict, elapsed_hours: float = 0.0,
               tariff: dict | None = None) -> dict:
        states = list(zone_states.values())
        occupancy = sum(state.occupancy.people_count for state in states)
        hvac_kw = sum(state.hvac_status.power_kw or 0.0 for state in states)
        occupancy_kw = occupancy * 0.015
        power_kw = self.base_load_kw + hvac_kw + occupancy_kw
        rate = float((tariff or {}).get("rate_per_kwh", 0.0))
        dt = max(0.0, float(elapsed_hours)) / self.expected_zone_updates
        self.energy_kwh += power_kw * dt
        timestamp = utc_now().isoformat()
        sample = {
            "timestamp": timestamp,
            "power_kw": round(power_kw, 3),
            "energy_kwh": round(self.energy_kwh, 5),
            "cost": round(self.energy_kwh * rate, 5),
            "occupancy": occupancy,
            "hvac_power_kw": round(hvac_kw, 3),
            "occupancy_power_kw": round(occupancy_kw, 3),
            "base_load_kw": self.base_load_kw,
            "zones": {
                state.zone.zone_id: {
                    "occupancy": state.occupancy.people_count,
                    "temperature": state.temperature,
                    "setpoint": state.hvac_status.present_value,
                    "hvac_mode": state.hvac_status.hvac_mode,
                    "hvac_power_kw": state.hvac_status.power_kw or 0.0,
                }
                for state in states
            },
            "tariff_rate_per_kwh": rate,
            "simulated": True,
        }
        # Clock timestamps can have equal precision on some platforms; keep sample order.
        if self.samples and timestamp <= self.samples[-1]["timestamp"]:
            timestamp = (datetime.fromisoformat(self.samples[-1]["timestamp"])
                         + timedelta(microseconds=1)).isoformat()
            sample["timestamp"] = timestamp
        self.samples.append(sample)
        EventTrace.log_event("ENERGY_UPDATE", "building", "demo_energy_telemetry", sample.copy())
        return sample

    def metrics(self) -> dict:
        """Summarize exactly the retained samples shown in the telemetry window."""
        powers = [sample["power_kw"] for sample in self.samples]
        return {
            "current_power_kw": powers[-1] if powers else 0.0,
            "average_power_kw": round(sum(powers) / len(powers), 3) if powers else 0.0,
            "peak_power_kw": max(powers, default=0.0),
        }

    def reset(self) -> None:
        self.samples.clear()
        self.energy_kwh = 0.0

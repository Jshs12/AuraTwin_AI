import math
from typing import Tuple

from backend.schemas.control import HVACSimulationResult

class HVACSimulator:
    """Deterministic, illustrative HVAC response model (not building physics)."""

    @staticmethod
    def simulate_tick(current_temp: float, target_setpoint: float, outdoor_temp: float = 30.0) -> Tuple[float, float]:
        """
        Simulate one time tick.
        Returns (new_temperature, power_kw)
        """
        # Very simple deterministic simulation:
        # Temperature drifts towards outdoor_temp if HVAC is off.
        # If setpoint < current_temp, HVAC cools it down.
        
        drift = (outdoor_temp - current_temp) * 0.05
        
        power_kw = 0.0
        cooling_effect = 0.0
        
        if current_temp > target_setpoint + 0.5:
            # Cooling
            cooling_effect = -0.5
            power_kw = 5.0
        elif current_temp < target_setpoint - 0.5:
            # Heating
            cooling_effect = 0.5
            power_kw = 6.0
            
        new_temp = current_temp + drift + cooling_effect
        
        return new_temp, power_kw

    @classmethod
    def simulate_response(
        cls,
        zone_id: str,
        current_temp: float,
        target_setpoint: float,
        *,
        outdoor_temp: float = 30.0,
        elapsed_hours: float = 1.0 / 60.0,
        accumulated_energy_kwh: float = 0.0,
    ) -> HVACSimulationResult:
        """Advance one deterministic minute and return simulated telemetry."""
        values = (current_temp, target_setpoint, outdoor_temp, elapsed_hours, accumulated_energy_kwh)
        if not all(math.isfinite(float(value)) for value in values) or elapsed_hours < 0:
            raise ValueError("HVAC simulation inputs must be finite and elapsed time non-negative.")
        next_temp, power_kw = cls.simulate_tick(current_temp, target_setpoint, outdoor_temp)
        if current_temp > target_setpoint + 0.5:
            mode = "COOLING"
        elif current_temp < target_setpoint - 0.5:
            mode = "HEATING"
        else:
            mode = "IDLE"
        energy = accumulated_energy_kwh + power_kw * elapsed_hours
        return HVACSimulationResult(
            zone_id=zone_id,
            current_temperature=round(next_temp, 2),
            target_setpoint=target_setpoint,
            hvac_mode=mode,
            fan_status=mode != "IDLE",
            power_kw=power_kw,
            energy_kwh=round(energy, 4),
        )

    @classmethod
    def simulate_occupancy_response(
        cls, zone_id: str, current_temp: float, target_setpoint: float,
        occupancy: int, *, elapsed_hours: float, accumulated_energy_kwh: float = 0.0,
    ) -> HVACSimulationResult:
        """Advance a deterministic HVAC tick with occupancy and thermal demand."""
        values = (current_temp, target_setpoint, elapsed_hours, accumulated_energy_kwh)
        if (not all(math.isfinite(float(value)) for value in values)
                or isinstance(occupancy, bool) or occupancy < 0 or elapsed_hours < 0):
            raise ValueError("HVAC simulation inputs must be finite and occupancy non-negative.")
        error = current_temp - target_setpoint
        if error > 0.5:
            mode = "COOLING"
            hvac_kw = 0.7 + min(error, 8.0) * 0.48 + occupancy * 0.018
            thermal_change = -min(0.5, max(0.02, error * 0.15))
        elif error < -0.5:
            mode = "HEATING"
            hvac_kw = 0.8 + min(abs(error), 8.0) * 0.5 + occupancy * 0.012
            thermal_change = min(0.5, max(0.02, abs(error) * 0.15))
        else:
            mode = "IDLE"
            hvac_kw = 0.12 + occupancy * 0.006
            thermal_change = 0.0
        # Occupants add a small, deterministic thermal gain; cooling/heating responds to it
        # on the next sample, making the energy signal causally dependent on zone state.
        minutes = max(0.25, elapsed_hours * 60.0)
        occupancy_gain = occupancy * 0.0015 * minutes
        next_temp = current_temp + thermal_change * minutes + occupancy_gain
        energy = accumulated_energy_kwh + hvac_kw * elapsed_hours
        return HVACSimulationResult(
            zone_id=zone_id, current_temperature=round(next_temp, 2),
            target_setpoint=target_setpoint, hvac_mode=mode,
            fan_status=mode != "IDLE", power_kw=round(hvac_kw, 3),
            energy_kwh=round(energy, 5),
        )

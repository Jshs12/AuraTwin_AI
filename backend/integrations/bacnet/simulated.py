"""Local BACnet-ready control simulation; this module speaks no BACnet protocol."""

import math
import threading
from datetime import datetime
from typing import Literal, Optional

from backend.core.events import EventTrace
from backend.core.interfaces import BuildingControlProvider
from backend.hvac.simulator import HVACSimulator
from backend.core.time import utc_now
from backend.schemas.control import BuildingControlState, ControlPoint, ControlResult, HVACCommand


class SimulatedBACnetBuildingControlProvider(BuildingControlProvider):
    """Deterministic provider boundary representing command/ack/telemetry flow."""

    provider_identity = "SIMULATED BACNET"

    @property
    def is_simulated(self) -> bool:
        return True

    @property
    def is_ready(self) -> bool:
        return self.failure_mode != "unavailable"

    def __init__(
        self,
        zone_ids: Optional[list[str]] = None,
        *,
        initial_temperature: float = 27.1,
        initial_setpoint: float = 24.0,
        initial_energy_kwh: float = 100.5,
        failure_mode: Optional[Literal["unavailable", "acknowledgement", "hvac_response"]] = None,
    ):
        self.failure_mode = failure_mode
        # Normal operation remains fail-closed from environment configuration.
        # An explicitly scoped simulated demo may temporarily supply the same
        # SafetyConstraintService used by its workflow and ControlService.
        self.command_safety = None
        self._strict_zones = zone_ids is not None
        self._states: dict[str, dict] = {}
        self._lock = threading.RLock()
        for zone_id in zone_ids or []:
            self.register_zone(zone_id, initial_temperature, initial_setpoint, initial_energy_kwh)

    def register_zone(self, zone_id: str, current_temperature: float = 27.1,
                      setpoint: float = 24.0, energy_kwh: float = 100.5) -> None:
        if not zone_id:
            raise ValueError("Zone ID is required.")
        if not all(math.isfinite(float(value)) for value in (current_temperature, setpoint, energy_kwh)):
            raise ValueError("Initial control values must be finite.")
        with self._lock:
            if zone_id in self._states:
                return
            _, initial_power = HVACSimulator.simulate_tick(current_temperature, setpoint)
            self._states[zone_id] = {
                "current_setpoint": float(setpoint),
                "requested_setpoint": float(setpoint),
                "previous_setpoint": None,
                "current_temperature": float(current_temperature),
                "occupancy": 0,
                "hvac_mode": "COOLING" if initial_power > 0 and current_temperature > setpoint else "IDLE",
                "fan_status": initial_power > 0,
                # Preserve the existing demo's initial simulated load; command
                # responses use the HVAC simulator's calculated load.
                "power_kw": 4.8 if initial_power > 0 else 0.0,
                "energy_kwh": float(energy_kwh),
                "last_command_timestamp": None,
                "observed_at": utc_now(),
                "setpoint_observed_at": utc_now(),
                "control_state": "READY",
            }

    def _state(self, zone_id: str) -> dict:
        if zone_id not in self._states:
            if self._strict_zones:
                raise KeyError(zone_id)
            self.register_zone(zone_id)
        return self._states[zone_id]

    def update_occupancy(self, zone_id: str, people_count: int) -> None:
        with self._lock:
            self._state(zone_id)["occupancy"] = int(people_count)

    def advance_simulation(self, zone_id: str, people_count: int, elapsed_hours: float = 0.0) -> None:
        """Advance simulated thermal/HVAC state from scenario input; emits telemetry only."""
        with self._lock:
            state = self._state(zone_id)
            state["occupancy"] = int(people_count)
            step = HVACSimulator.simulate_occupancy_response(
                zone_id, state["current_temperature"], state["current_setpoint"],
                int(people_count), elapsed_hours=elapsed_hours,
                accumulated_energy_kwh=state["energy_kwh"],
            )
            state.update({
                "current_temperature": step.current_temperature,
                "hvac_mode": step.hvac_mode,
                "fan_status": step.fan_status,
                "power_kw": step.power_kw,
                "energy_kwh": step.energy_kwh,
                "observed_at": step.timestamp,
            })
            EventTrace.log_event("HVAC_RESPONSE", zone_id, self.provider_identity, {
                "current_temperature": step.current_temperature,
                "setpoint": state["current_setpoint"], "occupancy": int(people_count),
                "hvac_mode": step.hvac_mode, "power_kw": step.power_kw,
                "elapsed_hours": elapsed_hours, "simulated": True,
            })

    def reset_simulation(self) -> None:
        """Restore deterministic initial demo readings without changing config."""
        with self._lock:
            for zone_id, state in self._states.items():
                _, initial_power = HVACSimulator.simulate_tick(27.1, 24.0)
                state.update({
                    "current_setpoint": 24.0, "requested_setpoint": 24.0,
                    "previous_setpoint": None, "current_temperature": 27.1,
                    "occupancy": 0,
                    "hvac_mode": "COOLING" if initial_power > 0 else "IDLE",
                    "fan_status": initial_power > 0, "power_kw": 4.8 if initial_power > 0 else 0.0,
                    "energy_kwh": 0.0, "last_command_timestamp": None,
                    "observed_at": utc_now(),
                    "setpoint_observed_at": utc_now(),
                    "control_state": "READY",
                })

    def read_status(self, zone_id: str) -> BuildingControlState:
        state = self.read_control_state(zone_id)
        if state is None:
            raise KeyError(zone_id)
        return state

    def read_control_state(self, zone_id: str) -> Optional[BuildingControlState]:
        with self._lock:
            try:
                s = self._state(zone_id)
            except KeyError:
                return None
            return BuildingControlState(
                zone_id=zone_id,
                object_id="SIMULATED_POINT:cooling_setpoint",
                present_value=s["current_setpoint"],
                provider=self.provider_identity,
                control_state=s["control_state"],
                requested_setpoint=s["requested_setpoint"],
                previous_setpoint=s["previous_setpoint"],
                current_temperature=s["current_temperature"],
                hvac_mode=s["hvac_mode"],
                fan_status=s["fan_status"],
                power_kw=s["power_kw"],
                energy_kwh=s["energy_kwh"],
                last_command_timestamp=s["last_command_timestamp"],
                timestamp=s["observed_at"], observed_at=s["observed_at"], simulated=True,
                setpoint_observed_at=s["setpoint_observed_at"],
            )

    def get_points(self, zone_id: str) -> list[ControlPoint]:
        with self._lock:
            s = self._state(zone_id)
            values = [
                ("temperature_present_value", s["current_temperature"], "°C", False),
                ("occupancy_present_value", s["occupancy"], "people", False),
                ("cooling_setpoint", s["current_setpoint"], "°C", True),
                ("heating_setpoint", 20.0, "°C", False),
                ("hvac_mode", s["hvac_mode"], None, False),
                ("fan_status", s["fan_status"], None, False),
            ]
            return [ControlPoint(zone_id=zone_id, point_name=name, present_value=value,
                                 units=units, writable=writable, provider=self.provider_identity)
                    for name, value, units, writable in values]

    def write_setpoint(self, command: HVACCommand) -> bool:
        return self.write_command(command).success

    def write_command(self, command: HVACCommand) -> ControlResult:
        with self._lock:
            try:
                state = self._state(command.zone_id)
            except KeyError:
                return self._failure(command, "REJECTED", "UNKNOWN_ZONE", "Unknown control zone.")

            previous = state["current_setpoint"]
            requested = command.setpoint
            if isinstance(requested, bool) or not isinstance(requested, (int, float)) or not math.isfinite(float(requested)):
                state["control_state"] = "REJECTED"
                return self._failure(command, "REJECTED", "INVALID_SETPOINT", "Setpoint must be finite and numeric.", previous)
            requested = float(requested)
            # Lazy import avoids a package initialization cycle: safety types
            # depend on shared intelligence schemas, while this provider is
            # itself loaded during API startup.
            from backend.safety.constraints import SafetyConstraintService
            safety = self.command_safety or SafetyConstraintService()
            rejection = safety.validate_command_values(
                command.model_dump(), command.zone_id, previous,
            )
            if rejection:
                state["control_state"] = "REJECTED"
                return self._failure(command, "REJECTED", "COMMAND_LIMIT_REJECTED", rejection, previous)

            state["requested_setpoint"] = requested
            state["last_command_timestamp"] = utc_now()
            state["setpoint_observed_at"] = state["last_command_timestamp"]
            if self.failure_mode == "unavailable":
                state["control_state"] = "FAILED"
                EventTrace.log_event("CONTROL_COMMAND_SENT", command.zone_id, self.provider_identity,
                                     {"command_id": command.command_id, "sent": False,
                                      "error_code": "PROVIDER_UNAVAILABLE"}, status="FAILED")
                return self._failure(command, "FAILED", "PROVIDER_UNAVAILABLE", "Simulated control provider is unavailable.", previous, log_unsent=False)
            EventTrace.log_event("CONTROL_COMMAND_SENT", command.zone_id, self.provider_identity,
                                 {"command_id": command.command_id, "requested_setpoint": requested})
            if self.failure_mode == "acknowledgement":
                state["control_state"] = "FAILED"
                EventTrace.log_event("CONTROL_ACKNOWLEDGED", command.zone_id, self.provider_identity,
                                     {"command_id": command.command_id, "acknowledged": False}, status="FAILED")
                return self._failure(command, "FAILED", "ACKNOWLEDGEMENT_FAILED", "Simulated command was not acknowledged.", previous, log_unsent=False)

            state["previous_setpoint"] = previous
            state["current_setpoint"] = requested
            EventTrace.log_event("CONTROL_ACKNOWLEDGED", command.zone_id, self.provider_identity,
                                 {"command_id": command.command_id, "acknowledged": True,
                                  "applied_setpoint": requested})
            if self.failure_mode == "hvac_response":
                state["control_state"] = "FAILED"
                EventTrace.log_event("HVAC_RESPONSE", command.zone_id, self.provider_identity,
                                     {"command_id": command.command_id, "hvac_mode": "OFFLINE"}, status="FAILED")
                return self._failure(command, "FAILED", "HVAC_RESPONSE_FAILED", "Simulated HVAC did not report a response.", previous, applied=requested, log_unsent=False)

            step = HVACSimulator.simulate_response(
                command.zone_id, state["current_temperature"], requested,
                accumulated_energy_kwh=state["energy_kwh"],
            )
            state.update({
                "current_temperature": step.current_temperature,
                "hvac_mode": step.hvac_mode,
                "fan_status": step.fan_status,
                "power_kw": step.power_kw,
                "energy_kwh": step.energy_kwh,
                "control_state": "APPLIED",
                "observed_at": step.timestamp,
            })
            EventTrace.log_event("HVAC_RESPONSE", command.zone_id, self.provider_identity,
                                 {"command_id": command.command_id, "applied_setpoint": requested,
                                  "current_temperature": step.current_temperature,
                                  "hvac_mode": step.hvac_mode, "power_kw": step.power_kw})
            EventTrace.log_event("ENERGY_UPDATE", command.zone_id, self.provider_identity,
                                 {"command_id": command.command_id, "power_kw": step.power_kw,
                                  "energy_kwh": step.energy_kwh, "simulated": True})
            return ControlResult(
                command_id=command.command_id, zone_id=command.zone_id,
                requested_setpoint=requested, applied_setpoint=requested,
                previous_setpoint=previous, success=True, status="SUCCESS",
                provider=self.provider_identity, simulated=True,
                # Align the acknowledged apply time with the simulated HVAC
                # observation persisted as the interval's starting boundary.
                timestamp=step.timestamp,
                recommendation_reference=command.recommendation_reference,
                hvac_mode=step.hvac_mode, current_temperature=step.current_temperature,
                power_kw=step.power_kw, energy_kwh=step.energy_kwh,
            )

    def _failure(self, command: HVACCommand, status: Literal["REJECTED", "FAILED"],
                 code: str, message: str, previous: Optional[float] = None,
                 applied: Optional[float] = None, *, log_unsent: bool = True) -> ControlResult:
        if log_unsent:
            EventTrace.log_event("CONTROL_COMMAND_SENT", command.zone_id, self.provider_identity,
                                 {"command_id": command.command_id, "sent": False,
                                  "error_code": code}, status=status)
        return ControlResult(
            command_id=command.command_id, zone_id=command.zone_id,
            requested_setpoint=(float(command.setpoint) if isinstance(command.setpoint, (int, float))
                                and not isinstance(command.setpoint, bool)
                                and math.isfinite(float(command.setpoint)) else None),
            applied_setpoint=applied,
            previous_setpoint=previous, success=False, status=status,
            provider=self.provider_identity, simulated=True,
            recommendation_reference=command.recommendation_reference,
            error_code=code, error_message=message,
        )

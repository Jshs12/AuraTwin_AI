from backend.schemas.state import ZoneState
from backend.schemas.zone import Zone
from backend.core.interfaces import OccupancyProvider, TemperatureProvider, EnergyProvider, BuildingControlProvider
from backend.core.events import EventTrace
from backend.core.paths import resolve_project_path
import json
from backend.services.data_quality import DataQualityGate
from backend.schemas.data_quality import QualityState
from backend.core.time import utc_now

class ZoneStateService:
    def __init__(
        self,
        occupancy_provider: OccupancyProvider,
        temperature_provider: TemperatureProvider,
        energy_provider: EnergyProvider,
        control_provider: BuildingControlProvider,
        data_quality_gate: DataQualityGate | None = None,
        configuration_repository=None,
        telemetry_service=None,
    ):
        self.occupancy_provider = occupancy_provider
        self.temperature_provider = temperature_provider
        self.energy_provider = energy_provider
        self.control_provider = control_provider
        self.data_quality_gate = data_quality_gate or DataQualityGate()
        self.configuration_repository = configuration_repository
        self.telemetry_service = telemetry_service
        self.zone_configurations = []
        # Explicit current inputs live in the existing ZoneState service only;
        # telemetry history is never read back to create runtime state.
        self._runtime_observations: dict[str, dict[str, dict]] = {}
        self._zones = self.refresh_configuration()
        
    def _load_zones(self):
        if self.configuration_repository is not None:
            configs = self.configuration_repository.runtime_zone_configs()
            self.zone_configurations = configs
            return {item.zone_id: Zone(zone_id=item.zone_id, name=item.name, type=item.type,
                capacity=item.capacity, area_m2=item.area_m2,
                comfort={"min_temperature": item.comfort["min_temperature"],
                         "max_temperature": item.comfort["max_temperature"]}) for item in configs}
        path = resolve_project_path("data/building/zones.json")
        if not path.exists():
            return {}
        with open(path, "r") as f:
            zones = json.load(f)
            return {z["zone_id"]: Zone(**z) for z in zones}

    def refresh_configuration(self):
        self.zone_configurations = []
        self._zones = self._load_zones()
        self._runtime_observations = {zone_id: values for zone_id, values in self._runtime_observations.items()
                                      if zone_id in self._zones}
        return self._zones

    def resolve_zone_id(self, zone_id: str) -> str:
        if zone_id in self._zones:
            return zone_id
        if self.configuration_repository is not None:
            config = self.configuration_repository.resolve_zone(zone_id)
            if config is not None:
                return config.zone_id
        raise ValueError(f"Zone {zone_id} not found")

    def get_zone_state(self, zone_id: str, occupancy_override=None, *, persist: bool = True) -> ZoneState:
        zone_id = self.resolve_zone_id(zone_id)
        zone = self._zones.get(zone_id)
        if not zone:
            raise ValueError(f"Zone {zone_id} not found")
            
        occ = occupancy_override or self.occupancy_provider.get_occupancy(zone_id)
        raw_occupancy_source = getattr(occ, "source", "unknown")
        occupancy_source = ("demo_scenario_simulation" if occupancy_override is not None else
            "yolo" if raw_occupancy_source == "yolo_occupancy_provider" else
            "mock" if raw_occupancy_source == "mock_occupancy_provider" else
            raw_occupancy_source if raw_occupancy_source != "unknown" else
            type(self.occupancy_provider).__name__.lower())
        if occ.source == "unknown":
            occ = occ.model_copy(update={"source": occupancy_source,
                "simulated": bool(getattr(occ, "simulated", False)) or "mock" in occupancy_source
                              or "simulation" in occupancy_source})
        EventTrace.log_event("OCCUPANCY_DETECTED", zone_id, "zone_state_service", {"people_count": occ.people_count})
        if hasattr(self.control_provider, "update_occupancy"):
            self.control_provider.update_occupancy(zone_id, occ.people_count)

        read_temperature = getattr(self.temperature_provider, "get_temperature_reading", None)
        temperature_reading = read_temperature(zone_id) if read_temperature else None
        temp = (temperature_reading.temperature if temperature_reading is not None
                else self.temperature_provider.get_temperature(zone_id))
        temperature_source = (temperature_reading.source if temperature_reading is not None
                              and temperature_reading.source != "unknown"
                              else type(self.temperature_provider).__name__.lower())
        temperature_simulated = (temperature_reading.simulated if temperature_reading is not None
                                 else "mock" in temperature_source)
        temperature_observed_at = (temperature_reading.observed_at if temperature_reading is not None
                                   else None)
        energy = self.energy_provider.get_energy(zone_id)
        hvac = self.control_provider.read_control_state(zone_id) or self.control_provider.read_status(zone_id)
        if hvac.current_temperature is not None:
            temp = hvac.current_temperature
            temperature_source = hvac.provider
            temperature_simulated = hvac.simulated
            temperature_observed_at = hvac.observed_at
        if hvac.power_kw is not None:
            simulated_energy = hvac.energy_kwh if hvac.energy_kwh is not None else energy.energy_kwh
            energy = energy.model_copy(update={
                "power_kw": hvac.power_kw,
                "energy_kwh": simulated_energy,
                "cost": round(simulated_energy * energy.tariff.rate_per_kwh, 4),
                "is_simulated": self.control_provider.is_simulated,
                "observed_at": hvac.observed_at,
                "source": hvac.provider,
            })
        
        state = ZoneState(
            zone=zone,
            occupancy=occ,
            temperature=temp,
            energy=energy,
            tariff=energy.tariff,
            hvac_status=hvac,
            occupancy_source=occupancy_source,
            temperature_source=temperature_source,
            temperature_observed_at=temperature_observed_at,
            temperature_simulated=temperature_simulated,
        )
        state = self._apply_runtime_observations(state, skip_occupancy=occupancy_override is not None,
            observations=self._runtime_observations.get(zone_id, {}))
        state.data_quality = self.data_quality_gate.assess_zone_state(state)
        if persist and self.telemetry_service is not None:
            self.telemetry_service.persist_zone_state(state)
        
        EventTrace.log_event("STATE_EVALUATED", zone_id, "zone_state_service",
            {"temperature": state.temperature, "setpoint": state.hvac_status.present_value})
        return state

    @staticmethod
    def _apply_runtime_observations(state: ZoneState, *, skip_occupancy: bool = False,
                                    observations: dict[str, dict] | None = None) -> ZoneState:
        values = observations or {}
        updates = {}
        occupancy = values.get("occupancy")
        if occupancy is not None and not skip_occupancy:
            count = int(occupancy["value"])
            capacity = state.zone.capacity
            percentage = count / capacity * 100 if capacity else 0.0
            level = "EMPTY" if count == 0 else "LOW" if percentage < 30 else "MEDIUM" if percentage < 70 else "HIGH"
            updates["occupancy"] = state.occupancy.model_copy(update={
                "people_count": count, "capacity": capacity,
                "occupancy_percentage": round(percentage, 1), "occupancy_state": level,
                "timestamp": occupancy["observed_at"], "observed_at": occupancy["observed_at"],
                "source": occupancy["source"], "simulated": occupancy["simulated"],
            })
            updates["occupancy_source"] = occupancy["source"]
        temperature = values.get("temperature")
        if temperature is not None:
            updates.update(temperature=float(temperature["value"]),
                temperature_observed_at=temperature["observed_at"],
                temperature_source=temperature["source"], temperature_simulated=temperature["simulated"])
        setpoint = values.get("cooling_setpoint")
        if setpoint is not None:
            updates["hvac_status"] = state.hvac_status.model_copy(update={
                "present_value": float(setpoint["value"]),
                "setpoint_observed_at": setpoint["observed_at"],
                "setpoint_source": setpoint["source"],
                "setpoint_simulated": setpoint["simulated"],
            })
        return state.model_copy(update=updates)

    def apply_runtime_observation(self, *, organization_id: str, building_id: str, floor_id: str,
                                  zone_id: str, zone_key: str, signal: str, value: float,
                                  observed_at, source: str, simulated: bool,
                                  quality_state: QualityState) -> bool:
        """Apply an already mapped and quality-checked current observation to ZoneState."""
        if signal not in {"occupancy", "temperature", "cooling_setpoint"}:
            EventTrace.log_event("RUNTIME_OBSERVATION_REJECTED", zone_key, "runtime_observation",
                                 {"signal": signal, "reason_code": "SIGNAL_HISTORICAL_ONLY"}, status="FAILED")
            return False
        if quality_state != QualityState.VALID or not source or observed_at is None:
            EventTrace.log_event("RUNTIME_OBSERVATION_REJECTED", zone_key, "runtime_observation",
                                 {"signal": signal, "reason_code": "OBSERVATION_NOT_VALID"}, status="FAILED")
            return False
        try:
            config = self.configuration_repository.resolve_zone(zone_id)
            if config is None or (config.building_id != building_id or config.floor_id != floor_id
                    or config.database_zone_id != zone_id or config.zone_key != zone_key):
                return False
            scope = self.configuration_repository.telemetry_scope(zone_id)
            if (scope is None or scope["organization_id"] != organization_id
                    or scope["building_id"] != building_id or scope["floor_id"] != floor_id
                    or scope["database_zone_id"] != zone_id):
                return False
            canonical_zone_id = self.resolve_zone_id(config.zone_id)
            base = self.get_zone_state(canonical_zone_id, persist=False)
        except (ValueError, AttributeError):
            return False

        current = dict(self._runtime_observations.get(canonical_zone_id, {}))
        current[signal] = {"value": value, "observed_at": observed_at,
                           "source": source, "simulated": bool(simulated)}
        candidate = self._apply_runtime_observations(base, observations=current)
        report = self.data_quality_gate.assess_zone_state(candidate, now=utc_now())
        failures = self.data_quality_gate.critical_failures(report)
        if failures:
            EventTrace.log_event("RUNTIME_OBSERVATION_REJECTED", canonical_zone_id, "runtime_observation",
                {"signal": signal, "reason_code": "CRITICAL_STATE_QUALITY_REJECTED",
                 "quality_state": quality_state.value, "simulated": bool(simulated),
                 "failed_signals": {name: {"state": item.state.value,
                     "reason_code": item.reason_code} for name, item in failures.items()}}, status="FAILED")
            return False
        self._runtime_observations.setdefault(canonical_zone_id, {})[signal] = current[signal]
        EventTrace.log_event("RUNTIME_OBSERVATION_APPLIED", canonical_zone_id, "runtime_observation",
            {"signal": signal, "quality_state": QualityState.VALID.value,
             "source": source, "simulated": bool(simulated)})
        return True

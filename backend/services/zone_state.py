from backend.schemas.state import ZoneState
from backend.schemas.zone import Zone
from backend.core.interfaces import OccupancyProvider, TemperatureProvider, EnergyProvider, BuildingControlProvider
from backend.core.events import EventTrace
from backend.core.paths import resolve_project_path
import json
from backend.services.data_quality import DataQualityGate

class ZoneStateService:
    def __init__(
        self,
        occupancy_provider: OccupancyProvider,
        temperature_provider: TemperatureProvider,
        energy_provider: EnergyProvider,
        control_provider: BuildingControlProvider,
        data_quality_gate: DataQualityGate | None = None,
    ):
        self.occupancy_provider = occupancy_provider
        self.temperature_provider = temperature_provider
        self.energy_provider = energy_provider
        self.control_provider = control_provider
        self.data_quality_gate = data_quality_gate or DataQualityGate()
        self._zones = self._load_zones()
        
    def _load_zones(self):
        path = resolve_project_path("data/building/zones.json")
        if not path.exists():
            return {}
        with open(path, "r") as f:
            zones = json.load(f)
            return {z["zone_id"]: Zone(**z) for z in zones}

    def get_zone_state(self, zone_id: str, occupancy_override=None) -> ZoneState:
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
        state.data_quality = self.data_quality_gate.assess_zone_state(state)
        
        EventTrace.log_event("STATE_EVALUATED", zone_id, "zone_state_service", {"temperature": temp, "setpoint": hvac.present_value})
        return state

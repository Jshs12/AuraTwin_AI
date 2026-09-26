from backend.schemas.state import ZoneState
from backend.schemas.zone import Zone
from backend.core.interfaces import OccupancyProvider, TemperatureProvider, EnergyProvider, BuildingControlProvider
from backend.core.events import EventTrace
from backend.core.paths import resolve_project_path
import json
from pathlib import Path

class ZoneStateService:
    def __init__(
        self,
        occupancy_provider: OccupancyProvider,
        temperature_provider: TemperatureProvider,
        energy_provider: EnergyProvider,
        control_provider: BuildingControlProvider
    ):
        self.occupancy_provider = occupancy_provider
        self.temperature_provider = temperature_provider
        self.energy_provider = energy_provider
        self.control_provider = control_provider
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
        EventTrace.log_event("OCCUPANCY_DETECTED", zone_id, "zone_state_service", {"people_count": occ.people_count})
        if hasattr(self.control_provider, "update_occupancy"):
            self.control_provider.update_occupancy(zone_id, occ.people_count)

        temp = self.temperature_provider.get_temperature(zone_id)
        energy = self.energy_provider.get_energy(zone_id)
        hvac = self.control_provider.read_control_state(zone_id) or self.control_provider.read_status(zone_id)
        if hvac.current_temperature is not None:
            temp = hvac.current_temperature
        if hvac.power_kw is not None:
            simulated_energy = hvac.energy_kwh if hvac.energy_kwh is not None else energy.energy_kwh
            energy = energy.model_copy(update={
                "power_kw": hvac.power_kw,
                "energy_kwh": simulated_energy,
                "cost": round(simulated_energy * energy.tariff.rate_per_kwh, 4),
                "is_simulated": self.control_provider.is_simulated,
            })
        
        state = ZoneState(
            zone=zone,
            occupancy=occ,
            temperature=temp,
            energy=energy,
            tariff=energy.tariff,
            hvac_status=hvac,
            occupancy_source=("demo_scenario_simulation" if occupancy_override is not None
                              else ("yolo" if "yolo" in type(self.occupancy_provider).__name__.lower()
                                    else "mock" if "mock" in type(self.occupancy_provider).__name__.lower()
                                    else type(self.occupancy_provider).__name__.lower())),
        )
        
        EventTrace.log_event("STATE_EVALUATED", zone_id, "zone_state_service", {"temperature": temp, "setpoint": hvac.present_value})
        return state

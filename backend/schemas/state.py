from pydantic import BaseModel, Field
from typing import Optional
from .zone import Zone
from .events import OccupancyEvent
from .energy import EnergyReading, Tariff
from .control import BuildingControlState

class ZoneState(BaseModel):
    zone: Zone
    occupancy: OccupancyEvent
    temperature: float = Field(..., description="Current temperature")
    energy: EnergyReading
    tariff: Tariff
    hvac_status: BuildingControlState
    occupancy_source: str = "occupancy_provider"

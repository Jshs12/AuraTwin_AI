from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from backend.core.time import utc_now

class OccupancyEvent(BaseModel):
    zone_id: str = Field(..., description="Zone identifier")
    people_count: int = Field(..., description="Number of people detected")
    capacity: int = Field(..., description="Zone capacity")
    occupancy_percentage: float = Field(..., description="Percentage of capacity used")
    occupancy_state: str = Field(..., description="State: EMPTY, LOW, MEDIUM, HIGH")
    timestamp: datetime = Field(default_factory=utc_now)

class TelemetryEvent(BaseModel):
    zone_id: str = Field(..., description="Zone identifier")
    temperature: float = Field(..., description="Current temperature")
    humidity: Optional[float] = Field(None, description="Current humidity")
    timestamp: datetime = Field(default_factory=utc_now)

class TemperatureReading(BaseModel):
    zone_id: str = Field(..., description="Zone identifier")
    temperature: float = Field(..., description="Current temperature")
    timestamp: datetime = Field(default_factory=utc_now)

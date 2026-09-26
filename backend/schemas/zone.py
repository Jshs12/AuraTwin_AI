from pydantic import BaseModel, Field
from typing import Optional

class ComfortLimits(BaseModel):
    min_temperature: float = Field(..., description="Minimum comfortable temperature")
    max_temperature: float = Field(..., description="Maximum comfortable temperature")

class Zone(BaseModel):
    zone_id: str = Field(..., description="Unique identifier for the zone")
    name: str = Field(..., description="Human-readable name")
    type: str = Field(..., description="Type of zone (e.g., classroom, office)")
    capacity: int = Field(..., description="Maximum occupancy capacity")
    area_m2: float = Field(..., description="Area in square meters")
    comfort: ComfortLimits = Field(..., description="Comfort limits for the zone")
    
    # Current state placeholders (would usually be dynamically joined)
    current_occupancy: int = Field(0, description="Current number of people")
    current_temperature: Optional[float] = Field(None, description="Current temperature in Celsius")
    current_setpoint: Optional[float] = Field(None, description="Current HVAC setpoint")

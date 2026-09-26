from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from backend.core.time import utc_now
from .zone import Zone
from .events import OccupancyEvent, TelemetryEvent
from .energy import EnergyReading, Tariff

class OptimizationRequest(BaseModel):
    zone_data: Zone
    occupancy: OccupancyEvent
    telemetry: TelemetryEvent
    energy: EnergyReading
    tariff: Tariff
    timestamp: datetime = Field(default_factory=utc_now)

class OptimizationRecommendation(BaseModel):
    zone_id: str = Field(..., description="Zone identifier")
    current_setpoint: float = Field(..., description="Current HVAC setpoint")
    recommended_setpoint: float = Field(..., description="Recommended HVAC setpoint")
    expected_power_kw: float = Field(..., description="Expected power consumption with new setpoint")
    estimated_hourly_cost: float = Field(..., description="Estimated cost per hour with new setpoint")
    current_temperature_status: str = Field(..., description="WITHIN_RANGE or OUT_OF_RANGE")
    recommended_setpoint_status: str = Field(..., description="WITHIN_RANGE or OUT_OF_RANGE")
    reason: str = Field(..., description="Reason for recommendation")
    source: str = Field(default="deterministic_optimizer", description="Source of the recommendation")
    timestamp: datetime = Field(default_factory=utc_now)

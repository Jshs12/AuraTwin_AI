from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class OptimizationInterval(BaseModel):
    interval_id: str
    organization_id: str | None = None
    building_id: str | None = None
    floor_id: str | None = None
    zone_id: str
    started_at: datetime
    ended_at: datetime | None = None
    starting_occupancy: int
    ending_occupancy: int | None = None
    starting_temperature: float
    ending_temperature: float | None = None
    previous_setpoint: float
    optimized_setpoint: float
    occupancy_observed_at: datetime | None = None
    duration_seconds: float | None = None
    energy_consumed_kwh: float | None = None
    cost_consumed: float | None = None
    tariff_rate_per_kwh: float | None = None
    currency: str | None = None
    status: Literal["ACTIVE", "COMPLETED"] = "ACTIVE"
    source: str
    simulated: bool
    energy_provenance: str | None = None
    reason: str = "HOLD_UNTIL_OCCUPANCY_CHANGE"

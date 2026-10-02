from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OptimizationInterval(BaseModel):
    """Persistable, provenance-aware record of one validated setpoint hold."""

    interval_id: str
    organization_id: str | None = None
    building_id: str | None = None
    floor_id: str | None = None
    zone_id: str
    database_zone_id: str | None = None
    status: Literal["ACTIVE", "COMPLETED"] = "ACTIVE"
    started_at: datetime
    ended_at: datetime | None = None
    starting_occupancy: int
    ending_occupancy: int | None = None
    starting_occupancy_observed_at: datetime | None = None
    ending_occupancy_observed_at: datetime | None = None
    previous_setpoint: float
    optimized_setpoint: float
    setpoint_observed_at: datetime | None = None
    starting_temperature: float
    ending_temperature: float | None = None
    starting_temperature_observed_at: datetime | None = None
    ending_temperature_observed_at: datetime | None = None
    duration_seconds: float | None = None
    starting_energy_kwh: float | None = None
    starting_energy_observed_at: datetime | None = None
    ending_energy_kwh: float | None = None
    ending_energy_observed_at: datetime | None = None
    energy_unit: str = "kWh"
    energy_consumed_kwh: float | None = None
    energy_status: Literal["PENDING", "AVAILABLE", "UNAVAILABLE", "INVALID"] = "UNAVAILABLE"
    energy_reason_code: str | None = "START_BOUNDARY_UNAVAILABLE"
    energy_source: str | None = None
    energy_quality: str | None = None
    energy_simulated: bool | None = None
    tariff_rate_per_kwh: float | None = None
    currency: str | None = None
    starting_tariff_observed_at: datetime | None = None
    ending_tariff_observed_at: datetime | None = None
    tariff_source: str | None = None
    tariff_quality: str | None = None
    tariff_simulated: bool | None = None
    cost_consumed: float | None = None
    cost_status: Literal["PENDING", "AVAILABLE", "UNAVAILABLE", "INVALID"] = "UNAVAILABLE"
    cost_reason_code: str | None = "TARIFF_COVERAGE_UNAVAILABLE"
    quality_state: str = "VALID"
    source: str
    simulated: bool
    reason: str = Field(default="HOLD_UNTIL_OCCUPANCY_CHANGE", max_length=160)

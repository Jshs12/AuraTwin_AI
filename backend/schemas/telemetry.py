from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class TelemetrySignal(StrEnum):
    OCCUPANCY = "occupancy"
    TEMPERATURE = "temperature"
    POWER = "power"
    ENERGY = "energy"
    COST = "cost"
    TARIFF_RATE = "tariff_rate"
    COOLING_SETPOINT = "cooling_setpoint"


class TelemetryObservation(BaseModel):
    """A scalar observation and its resolved tenant scope; no raw payloads/images."""

    model_config = ConfigDict(frozen=True)

    organization_id: str
    building_id: str
    floor_id: str
    zone_id: str
    signal: TelemetrySignal
    value: float
    unit: str
    observed_at: datetime
    source: str | None = None
    quality_state: str | None = None
    simulated: bool | None = None
    ingested_at: datetime | None = None


class TelemetryHistoryResponse(BaseModel):
    observations: list[TelemetryObservation] = Field(default_factory=list)


class TelemetryAggregation(BaseModel):
    signal: TelemetrySignal
    count: int
    minimum: float | None = None
    maximum: float | None = None
    average: float | None = None


class TelemetryAnalyticsResponse(BaseModel):
    observations: list[TelemetryObservation] = Field(default_factory=list)
    aggregations: list[TelemetryAggregation] = Field(default_factory=list)
    query: dict[str, str | None]
    truncated: bool = False

"""Process-local Edge Connector contracts; no credentials or control commands."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EdgeMode(StrEnum):
    SIMULATED = "simulated"
    REAL = "real"


class EdgeLifecycleState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class EdgeCapability(StrEnum):
    READ_BACNET = "READ_BACNET"
    READ_CAMERA = "READ_CAMERA"
    READ_ENERGY_METER = "READ_ENERGY_METER"
    FORWARD_OBSERVATIONS = "FORWARD_OBSERVATIONS"
    HEARTBEAT = "HEARTBEAT"


class EdgeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    building_id: str = Field(min_length=1, max_length=160)
    expected_organization_id: str | None = Field(default=None, max_length=160)
    name: str = Field(default="AuraTwin Edge Connector", min_length=1, max_length=160)
    version: str = Field(default="13.4A", min_length=1, max_length=40)
    mode: EdgeMode
    max_buffer_messages: int = Field(default=100, ge=1, le=10000)


class EdgeObservationEnvelope(BaseModel):
    """Versioned outbound scalar contract. Raw integration credentials are not representable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1.0"
    message_id: UUID
    edge_id: str
    organization_id: str
    building_id: str
    integration_id: str
    device_id: str
    point_id: str
    zone_id: str
    signal: str
    value: float = Field(allow_inf_nan=False)
    unit: str
    observation_timestamp: datetime
    ingestion_timestamp: datetime
    source: str
    quality: str
    simulated: bool

    @field_validator("observation_timestamp", "ingestion_timestamp")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Edge observation timestamps must include a timezone")
        return value


class EdgeHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str
    organization_id: str
    building_id: str
    connector_version: str
    timestamp: datetime
    lifecycle_state: EdgeLifecycleState
    transport_state: str
    queue_depth: int = Field(ge=0)
    last_observation_timestamp: datetime | None = None
    simulated: bool
    capabilities: tuple[EdgeCapability, ...]


class EdgeHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str | None = None
    organization_id: str | None = None
    building_id: str
    name: str | None = None
    version: str
    mode: EdgeMode
    state: EdgeLifecycleState
    transport_state: str
    queue_depth: int = Field(ge=0)
    max_buffer_messages: int
    last_heartbeat: datetime | None = None
    last_observation_forwarded: datetime | None = None
    simulated: bool
    capabilities: tuple[EdgeCapability, ...] = ()
    healthy: bool
    reason_code: str | None = None

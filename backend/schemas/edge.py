"""Process-local Edge Connector contracts; no credentials or control commands."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit
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
    version: str = Field(default="13.4B", min_length=1, max_length=40)
    mode: EdgeMode
    max_buffer_messages: int = Field(default=100, ge=1, le=10000)
    max_attempts: int = Field(default=5, ge=1, le=20)
    retry_base_seconds: float = Field(default=1.0, ge=0, le=300)
    retry_max_seconds: float = Field(default=30.0, ge=0, le=3600)
    connection_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    batch_size: int = Field(default=25, ge=1, le=500)
    outbound_endpoint: str | None = Field(default=None, max_length=1000)
    identity_reference: str | None = Field(default=None, max_length=240)

    @field_validator("outbound_endpoint")
    @classmethod
    def outbound_endpoint_must_be_https(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Configured outbound transport endpoint must be HTTPS without embedded credentials")
        return value


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
    last_successful_delivery: datetime | None = None
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
    oldest_queued_observation: datetime | None = None
    newest_queued_observation: datetime | None = None
    last_successful_delivery: datetime | None = None
    last_delivery_failure: datetime | None = None
    retry_count: int = Field(default=0, ge=0)
    queue_full: bool = False
    delivery_status: str = "IDLE"


class EdgeDeliveryStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class EdgeDeliveryAcknowledgement(BaseModel):
    """Application-level ACK; simulated ACKs are explicitly local and not cloud delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: UUID
    status: EdgeDeliveryStatus
    server_received_at: datetime
    schema_version: str = "1.0"
    ingestion_id: str | None = None

    @field_validator("server_received_at")
    @classmethod
    def received_timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Acknowledgement timestamp must include a timezone")
        return value

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: str) -> str:
        if value != "1.0":
            raise ValueError("Unsupported acknowledgement schema version")
        return value

"""Provider-neutral scalar observation contract (never carries image/video payloads)."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from backend.schemas.data_quality import QualityState


class ProviderObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    integration_id: str
    device_id: str
    point_mapping_id: str
    observed_at: datetime
    value: float = Field(strict=True, allow_inf_nan=False)
    # A source is a stable identifier, never a URL/connection string or free-form payload.
    source: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    simulated: bool = Field(strict=True)
    quality_state: QualityState | None = None
    runtime_input: bool = False
    # Optional provider hints; the confirmed mapping remains authoritative.
    signal: str | None = None
    unit: str | None = None

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Provider observation timestamp must include a timezone")
        return value


class ObservationIngestionResult(BaseModel):
    accepted: bool
    persisted: bool = False
    duplicate: bool = False
    signal: str | None = None
    zone_id: str | None = None
    observed_at: datetime | None = None
    source: str | None = None
    simulated: bool | None = None
    quality_state: QualityState | None = None
    reason_code: str | None = None
    runtime_input_applied: bool = False
    runtime_applicable: bool = False
    organization_id: str | None = None
    building_id: str | None = None
    floor_id: str | None = None
    unit: str | None = None
    ingested_at: datetime | None = None
    value: float | None = None
    integration_id: str | None = None
    device_id: str | None = None
    point_mapping_id: str | None = None
    protocol: str | None = None


class SimulatedObservationRequest(BaseModel):
    """Explicit simulated reading; the API supplies mapping identity and simulated provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    observed_at: datetime
    value: float = Field(strict=True, allow_inf_nan=False)

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Observation timestamp must include a timezone")
        return value

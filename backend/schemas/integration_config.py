"""Validated request schemas for integration commissioning metadata."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _safe_configuration(value: dict[str, Any]) -> dict[str, Any]:
    forbidden = {"password", "secret", "api_key", "apikey", "token", "authorization", "credential", "username"}
    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in forbidden or any(term in normalized for term in ("password", "secret", "api_key", "token", "authorization", "credential")):
                    raise ValueError("Secret material must not be stored in integration configuration")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and "://" in item:
            from urllib.parse import parse_qsl, urlsplit
            parsed = urlsplit(item)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("Connection URLs must not contain embedded credentials")
            if any(key.lower() in forbidden for key, _ in parse_qsl(parsed.query)):
                raise ValueError("Connection URLs must not contain credential query parameters")
    visit(value)
    return value


class IntegrationCreate(StrictInput):
    name: str = Field(min_length=1, max_length=160)
    integration_type: Literal["BACNET", "CAMERA", "ENERGY_METER"]
    configuration: dict[str, Any] = Field(default_factory=dict)
    credential_reference: str | None = Field(default=None, max_length=500,
        pattern=r"^(?:vault|secret|env|arn)://[A-Za-z0-9._:/-]+$")

    @field_validator("configuration")
    @classmethod
    def validate_configuration(cls, value):
        return _safe_configuration(value)


class IntegrationUpdate(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    configuration: dict[str, Any] | None = None
    credential_reference: str | None = Field(default=None, max_length=500,
        pattern=r"^(?:vault|secret|env|arn)://[A-Za-z0-9._:/-]+$")
    status: Literal["CONFIGURED", "DISABLED"] | None = None

    @field_validator("configuration")
    @classmethod
    def validate_configuration(cls, value):
        return _safe_configuration(value) if value is not None else value

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("At least one integration field is required")
        return self


class DeviceCreate(StrictInput):
    external_device_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    device_type: str = Field(min_length=1, max_length=80)
    zone_id: str | None = None
    manufacturer: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)


class DeviceUpdate(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    device_type: str | None = Field(default=None, min_length=1, max_length=80)
    zone_id: str | None = None
    manufacturer: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    status: Literal["CONFIGURED", "DISABLED"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("At least one device field is required")
        return self


class PointCreate(StrictInput):
    zone_id: str | None = None
    external_point_id: str = Field(min_length=1, max_length=250)
    logical_signal: str = Field(min_length=1, max_length=100)
    data_type: str = Field(min_length=1, max_length=40)
    unit: str | None = Field(default=None, max_length=40)
    readable: bool = True
    writable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    mapping_status: Literal["UNMAPPED", "SUGGESTED"] = "UNMAPPED"
    mapping_confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value):
        return _safe_configuration(value)

    @model_validator(mode="after")
    def direction_and_confidence(self):
        if not self.readable and not self.writable:
            raise ValueError("A point must be readable or writable")
        if self.mapping_status != "SUGGESTED" and self.mapping_confidence is not None:
            raise ValueError("Confidence is only valid for a suggested mapping")
        if self.mapping_status == "SUGGESTED" and self.mapping_confidence is None:
            raise ValueError("Suggested mappings require an explicitly supplied confidence")
        return self


class PointUpdate(StrictInput):
    zone_id: str | None = None
    logical_signal: str | None = Field(default=None, min_length=1, max_length=100)
    data_type: str | None = Field(default=None, min_length=1, max_length=40)
    unit: str | None = Field(default=None, max_length=40)
    readable: bool | None = None
    writable: bool | None = None
    metadata: dict[str, Any] | None = None
    mapping_status: Literal["UNMAPPED", "SUGGESTED", "CONFIRMED", "REJECTED", "INACTIVE"] | None = None
    mapping_confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value):
        return _safe_configuration(value) if value is not None else value

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("At least one point field is required")
        return self

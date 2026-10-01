"""HTTP request schemas for persistent building configuration."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildingCreate(ConfigurationInput):
    organization_id: str
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    address: dict = Field(default_factory=dict)


class BuildingUpdate(ConfigurationInput):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    slug: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    address: dict | None = None

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Provide at least one non-null building field")
        return self


class FloorCreate(ConfigurationInput):
    name: str = Field(min_length=1, max_length=160)
    floor_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    level_number: int | None = None


class FloorUpdate(ConfigurationInput):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    floor_key: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    level_number: int | None = None

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one floor field")
        if any(getattr(self, field) is None for field in self.model_fields_set if field != "level_number"):
            raise ValueError("Floor name and key cannot be null")
        return self


class ComfortRange(BaseModel):
    min_temperature: float = Field(allow_inf_nan=False)
    max_temperature: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.min_temperature >= self.max_temperature:
            raise ValueError("comfort minimum must be less than maximum")
        return self


class ZoneCreate(ConfigurationInput):
    zone_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=80)
    capacity: int = Field(ge=0)
    area_m2: float = Field(ge=0, allow_inf_nan=False)
    comfort: ComfortRange


class ZoneUpdate(ConfigurationInput):
    zone_key: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: str | None = Field(default=None, min_length=1, max_length=80)
    capacity: int | None = Field(default=None, ge=0)
    area_m2: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    comfort: ComfortRange | None = None

    @model_validator(mode="after")
    def non_empty_patch(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Configuration fields cannot be null")
        return self

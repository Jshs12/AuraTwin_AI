from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional, Any, Literal
from uuid import uuid4
from backend.core.time import utc_now

class HVACCommand(BaseModel):
    zone_id: str = Field(..., description="Zone identifier")
    setpoint: float = Field(..., description="Target setpoint")
    source: str = Field(..., description="Source of command, e.g. optimization_engine, manual")
    timestamp: datetime = Field(default_factory=utc_now)
    command_id: str = Field(default_factory=lambda: str(uuid4()))
    recommendation_reference: Optional[str] = None

class BuildingControlState(BaseModel):
    """Provider-neutral control and telemetry snapshot."""

    zone_id: str = Field(..., description="Zone identifier")
    object_id: str = Field(..., description="Legacy point label; not necessarily a BACnet object ID")
    present_value: Any = Field(..., description="Current applied setpoint")
    timestamp: datetime = Field(default_factory=utc_now)
    provider: str = "mock_building_control_provider"
    control_state: Literal["READY", "APPLIED", "REJECTED", "FAILED"] = "READY"
    requested_setpoint: Optional[float] = None
    previous_setpoint: Optional[float] = None
    current_temperature: Optional[float] = None
    hvac_mode: Optional[Literal["COOLING", "HEATING", "IDLE", "OFFLINE"]] = None
    fan_status: Optional[bool] = None
    power_kw: Optional[float] = None
    energy_kwh: Optional[float] = None
    last_command_timestamp: Optional[datetime] = None


# Backward-compatible schema name retained for existing API consumers.
BACnetReadResult = BuildingControlState


class ControlResult(BaseModel):
    """Provider-neutral acknowledgement for a simulated or future control write."""

    model_config = ConfigDict(extra="forbid")

    command_id: str
    zone_id: str
    requested_setpoint: Optional[float]
    applied_setpoint: Optional[float] = None
    previous_setpoint: Optional[float] = None
    success: bool
    status: Literal["SUCCESS", "REJECTED", "FAILED"]
    provider: str
    simulated: bool = True
    timestamp: datetime = Field(default_factory=utc_now)
    command: str = "SET_COOLING_SETPOINT"
    recommendation_reference: Optional[str] = None
    hvac_mode: Optional[str] = None
    current_temperature: Optional[float] = None
    power_kw: Optional[float] = None
    energy_kwh: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class ControlPoint(BaseModel):
    """Provider-neutral semantic point; no BACnet instance numbers are implied."""

    zone_id: str
    point_name: str
    present_value: Any
    units: Optional[str] = None
    writable: bool = False
    provider: str
    timestamp: datetime = Field(default_factory=utc_now)


class HVACSimulationResult(BaseModel):
    zone_id: str
    current_temperature: float
    target_setpoint: float
    hvac_mode: Literal["COOLING", "HEATING", "IDLE"]
    fan_status: bool
    power_kw: float
    energy_kwh: float
    timestamp: datetime = Field(default_factory=utc_now)

class ControlEvent(BaseModel):
    event_id: str = Field(..., description="Unique event identifier")
    event_type: str = Field(..., description="Type of event: OCCUPANCY_DETECTED, BACNET_WRITE, etc.")
    zone_id: str = Field(..., description="Zone identifier")
    timestamp: datetime = Field(default_factory=utc_now)
    source: str = Field(..., description="Source system/module")
    payload: dict = Field(default_factory=dict, description="Event payload data")
    status: str = Field(..., description="Event status: SUCCESS, FAILED, PENDING")

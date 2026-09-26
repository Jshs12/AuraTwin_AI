from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from backend.core.time import utc_now


class IntelligenceContext(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    zone_id: str
    zone_type: str
    occupancy_count: int
    occupancy_percentage: float
    occupancy_level: str
    current_temperature: float
    comfort_min_temperature: float
    comfort_max_temperature: float
    current_hvac_setpoint: float
    current_power_kw: float
    energy_kwh: float
    energy_cost: float
    tariff_rate_per_kwh: float
    tariff_currency: str
    timestamp: datetime = Field(default_factory=utc_now)
    provenance: dict[str, str]
    historical_context: Optional[dict[str, Any]] = None
    previous_recommendation: Optional[dict[str, Any]] = None


class IntelligenceRecommendation(BaseModel):
    zone_id: str
    recommended_setpoint: Any
    rationale: str
    confidence: Any
    provider: str
    model_source: str
    timestamp: datetime = Field(default_factory=utc_now)
    context_reference: dict[str, Any]
    action_type: str = "setpoint_adjustment"


class SafetyValidationResult(BaseModel):
    outcome: Literal["VALIDATED", "REJECTED", "FALLBACK"]
    original_recommendation: Optional[dict[str, Any]] = None
    validated_setpoint: Optional[float] = None
    rejection_reason: Optional[str] = None
    fallback_reason: Optional[str] = None
    validation_timestamp: datetime = Field(default_factory=utc_now)
    source: str


class RecommendationDecision(BaseModel):
    zone_id: str
    intelligence_recommendation: Optional[IntelligenceRecommendation] = None
    deterministic_recommendation: Optional[dict[str, Any]] = None
    validation: SafetyValidationResult
    recommendation_kind: Literal["intelligence", "deterministic_fallback", "rejected"]


class RecommendationSubmission(BaseModel):
    """Untrusted client submission; its validation fields are deliberately omitted."""
    zone_id: str
    intelligence_recommendation: Optional[dict[str, Any]] = None
    deterministic_recommendation: Optional[dict[str, Any]] = None
    recommendation_kind: Optional[str] = None

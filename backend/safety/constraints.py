import math
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from backend.intelligence.schemas import IntelligenceRecommendation, SafetyValidationResult
from backend.schemas.state import ZoneState
from backend.core.time import utc_now


class SafetyConstraintService:
    """Deterministic gate for every proposed setpoint before control."""

    HARD_MIN_SETPOINT = 16.0
    HARD_MAX_SETPOINT = 30.0
    MAX_SETPOINT_CHANGE = 2.0
    MIN_CONFIDENCE = 0.60
    ALLOWED_ACTIONS = {"setpoint_adjustment"}

    def validate(self, recommendation: Any, state: ZoneState) -> SafetyValidationResult:
        raw = recommendation.model_dump() if isinstance(recommendation, IntelligenceRecommendation) else recommendation
        if not isinstance(raw, dict):
            return self._rejected(None, "Recommendation must be a structured object.")
        try:
            rec = IntelligenceRecommendation.model_validate(raw)
        except (ValidationError, TypeError, ValueError):
            # Pydantic error strings can include untrusted input values. Keep
            # them out of API responses and event payloads.
            return self._rejected(None, "Malformed recommendation structure.")

        setpoint = rec.recommended_setpoint
        if rec.zone_id != state.zone.zone_id:
            return self._rejected(None, "Recommendation zone ID does not match the current zone.")
        if rec.action_type not in self.ALLOWED_ACTIONS:
            return self._rejected(None, "Recommendation action is not allowed.")
        if isinstance(setpoint, bool) or not isinstance(setpoint, (int, float)):
            return self._rejected(None, "Recommended setpoint must be numeric.")
        setpoint = float(setpoint)
        if not math.isfinite(setpoint):
            return self._rejected(None, "Recommended setpoint must be finite.")
        if not isinstance(rec.confidence, (int, float)) or isinstance(rec.confidence, bool) or not math.isfinite(float(rec.confidence)) or not 0 <= float(rec.confidence) <= 1:
            return self._rejected(None, "Confidence must be a finite number between 0 and 1.")
        if float(rec.confidence) < self.MIN_CONFIDENCE:
            return self._rejected(None, f"Confidence is below the minimum of {self.MIN_CONFIDENCE}.")
        if not self.HARD_MIN_SETPOINT <= setpoint <= self.HARD_MAX_SETPOINT:
            return self._rejected(None, f"Setpoint must be within hard bounds [{self.HARD_MIN_SETPOINT}, {self.HARD_MAX_SETPOINT}] °C.")
        if not state.zone.comfort.min_temperature <= setpoint <= state.zone.comfort.max_temperature:
            return self._rejected(None, "Setpoint is outside the zone comfort limits.")
        current = state.hvac_status.present_value
        if not isinstance(current, (int, float)) or not math.isfinite(float(current)):
            return self._rejected(None, "Current HVAC setpoint is unavailable or invalid.")
        if abs(setpoint - float(current)) > self.MAX_SETPOINT_CHANGE:
            return self._rejected(None, f"Setpoint change exceeds the {self.MAX_SETPOINT_CHANGE} °C per-decision limit.")

        # Store only the canonical fields needed for fresh-state revalidation.
        # Submitted dictionaries may contain arbitrary extra values, and
        # context/rationale text must not be copied into shared event payloads.
        safe_original = {
            "zone_id": rec.zone_id,
            "recommended_setpoint": setpoint,
            "rationale": "Validated advisory.",
            "confidence": float(rec.confidence),
            "provider": self._safe_identifier(rec.provider),
            "model_source": self._safe_identifier(rec.model_source),
            "timestamp": rec.timestamp.isoformat(),
            "context_reference": {"zone_id": rec.zone_id},
            "action_type": rec.action_type,
        }

        return SafetyValidationResult(
            outcome="VALIDATED", original_recommendation=safe_original,
            validated_setpoint=setpoint, source=safe_original["provider"],
        )

    def validate_fallback(self, recommendation: Any, state: ZoneState, reason: str) -> SafetyValidationResult:
        result = self.validate(recommendation, state)
        if result.outcome != "VALIDATED":
            result.fallback_reason = f"{reason}; deterministic fallback rejected: {result.rejection_reason}"
            return result
        result.outcome = "FALLBACK"
        result.fallback_reason = reason
        result.source = "deterministic_optimizer"
        return result

    @staticmethod
    def _rejected(raw: Any, reason: str) -> SafetyValidationResult:
        return SafetyValidationResult(
            outcome="REJECTED", original_recommendation=None,
            rejection_reason=reason, validation_timestamp=utc_now(), source="safety_constraint_service",
        )

    @staticmethod
    def _safe_identifier(value: str) -> str:
        known = {
            "lyzr", "mock_intelligence_provider", "deterministic_optimizer",
            "test_provider", "test", "unit_test",
        }
        if isinstance(value, str) and value in known:
            return value
        return "external_advisory"

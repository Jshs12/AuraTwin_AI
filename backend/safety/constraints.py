import math
import os
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from backend.intelligence.schemas import IntelligenceRecommendation, SafetyValidationResult
from backend.schemas.state import ZoneState
from backend.core.time import utc_now


class SafetyConstraintService:
    """Deterministic recommendation and final command safety gate.

    Command limits have no production defaults. An incomplete or invalid
    configured policy rejects safely.
    """

    MIN_CONFIDENCE = 0.60
    ALLOWED_ACTIONS = {"setpoint_adjustment"}

    def __init__(self, *, min_setpoint: float | None = None,
                 max_setpoint: float | None = None,
                 max_setpoint_delta: float | None = None):
        self.min_setpoint = self._configured_value(min_setpoint, "COMMAND_LIMIT_MIN_SETPOINT")
        self.max_setpoint = self._configured_value(max_setpoint, "COMMAND_LIMIT_MAX_SETPOINT")
        self.max_setpoint_delta = self._configured_value(max_setpoint_delta, "COMMAND_LIMIT_MAX_DELTA")

    @staticmethod
    def _configured_value(value: float | None, name: str) -> float | None:
        raw = os.getenv(name) if value is None else value
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        if isinstance(raw, bool):
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _command_policy_error(self) -> str | None:
        if self.min_setpoint is None or self.max_setpoint is None or self.max_setpoint_delta is None:
            return "Command limit policy is incomplete; set all COMMAND_LIMIT_* values."
        if self.min_setpoint >= self.max_setpoint or self.max_setpoint_delta <= 0:
            return "Command limit policy is invalid."
        return None

    @property
    def command_policy_ready(self) -> bool:
        return self._command_policy_error() is None

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
        policy_error = self._command_policy_error()
        if policy_error:
            return self._rejected(None, policy_error)
        if not self.min_setpoint <= setpoint <= self.max_setpoint:
            return self._rejected(None, f"Setpoint must be within configured bounds [{self.min_setpoint:g}, {self.max_setpoint:g}] °C.")
        if not state.zone.comfort.min_temperature <= setpoint <= state.zone.comfort.max_temperature:
            return self._rejected(None, "Setpoint is outside the zone comfort limits.")
        current = state.hvac_status.present_value
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(float(current)):
            return self._rejected(None, "Current HVAC setpoint is unavailable or invalid.")
        if abs(setpoint - float(current)) > self.max_setpoint_delta:
            return self._rejected(None, f"Setpoint change exceeds the configured {self.max_setpoint_delta:g} °C per-command limit.")

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

    def validate_command(self, command: Any, state: ZoneState) -> SafetyValidationResult:
        """Validate the exact command payload against current state before write."""
        raw = command.model_dump() if hasattr(command, "model_dump") else command
        if not isinstance(raw, dict):
            return self._rejected(None, "Control command must be a structured object.")
        values_error = self.validate_command_values(raw, state.zone.zone_id,
                                                    state.hvac_status.present_value)
        if values_error:
            return self._rejected(None, values_error)
        setpoint = float(raw["setpoint"])
        if not state.zone.comfort.min_temperature <= setpoint <= state.zone.comfort.max_temperature:
            return self._rejected(None, "Control command is outside the zone comfort limits.")
        safe = {"zone_id": state.zone.zone_id, "recommended_setpoint": setpoint,
                "rationale": "Validated command.", "confidence": 1.0,
                "provider": "control_service", "model_source": "command_limit_gate",
                "action_type": raw["action_type"], "context_reference": {"zone_id": state.zone.zone_id}}
        return SafetyValidationResult(outcome="VALIDATED", original_recommendation=safe,
            validated_setpoint=setpoint, source="command_limit_gate")

    def validate_command_values(self, raw: Any, zone_id: str,
                                current_setpoint: Any) -> str | None:
        """Shared command checks usable at provider boundaries without zone comfort data."""
        if not isinstance(raw, dict):
            return "Control command must be a structured object."
        if raw.get("zone_id") != zone_id:
            return "Control command zone ID does not match the current zone."
        if raw.get("action_type") not in self.ALLOWED_ACTIONS:
            return "Control command action is not allowed."
        setpoint = raw.get("setpoint")
        if isinstance(setpoint, bool) or not isinstance(setpoint, (int, float)):
            return "Control command setpoint must be numeric."
        setpoint = float(setpoint)
        if not math.isfinite(setpoint):
            return "Control command setpoint must be finite."
        policy_error = self._command_policy_error()
        if policy_error:
            return policy_error
        if not self.min_setpoint <= setpoint <= self.max_setpoint:
            return "Control command is outside configured absolute bounds."
        current = current_setpoint
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(float(current)):
            return "Current HVAC setpoint is unavailable or invalid."
        if abs(setpoint - float(current)) > self.max_setpoint_delta:
            return "Control command exceeds the configured per-command change limit."
        return None

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

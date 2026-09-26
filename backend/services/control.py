import math
import os
from datetime import datetime
from uuid import uuid4

from backend.core.events import EventTrace
from backend.core.interfaces import BuildingControlProvider
from backend.intelligence.schemas import SafetyValidationResult
from backend.safety.constraints import SafetyConstraintService
from backend.schemas.control import ControlResult, HVACCommand
from backend.schemas.state import ZoneState
from backend.core.time import utc_now


class ControlService:
    """Authoritative safety gate and command lifecycle for building control."""

    def __init__(self, provider: BuildingControlProvider, safety: SafetyConstraintService | None = None):
        self.provider = provider
        self.safety = safety or SafetyConstraintService()
        self.last_results: dict[str, ControlResult] = {}
        try:
            self.recommendation_ttl_seconds = float(os.getenv("RECOMMENDATION_TTL_SECONDS", "60"))
            if not math.isfinite(self.recommendation_ttl_seconds) or self.recommendation_ttl_seconds <= 0:
                self.recommendation_ttl_seconds = 60.0
        except ValueError:
            self.recommendation_ttl_seconds = 60.0

    def apply_validated_recommendation(self, validation: SafetyValidationResult, state: ZoneState) -> bool:
        """Backward-compatible boolean API; details live in the result method."""
        return self.apply_validated_recommendation_result(validation, state).success

    def apply_validated_recommendation_result(
        self, validation: SafetyValidationResult, state: ZoneState
    ) -> ControlResult:
        command_id = str(uuid4())
        requested = validation.validated_setpoint
        if requested is None:
            requested = float(state.hvac_status.present_value)
        original = validation.original_recommendation or {}
        reference = str(original.get("timestamp") or original.get("context_reference", {}).get("recommendation_id") or "") or None
        EventTrace.log_event(
            "CONTROL_COMMAND_REQUESTED", state.zone.zone_id, "control_service",
            {"command_id": command_id, "recommendation_reference": reference,
             "requested_setpoint": requested, "provider": self.provider.provider_identity},
        )

        if validation.outcome not in {"VALIDATED", "FALLBACK"} or validation.validated_setpoint is None:
            reason = validation.rejection_reason or "Recommendation has no validated setpoint."
            EventTrace.log_event("CONTROL_VALIDATION", state.zone.zone_id, "control_service",
                                 {"command_id": command_id, "outcome": "REJECTED", "reason": reason}, status="FAILED")
            return self._remember(ControlResult(
                command_id=command_id, zone_id=state.zone.zone_id,
                requested_setpoint=float(requested), success=False, status="REJECTED",
                provider=self.provider.provider_identity, simulated=self.provider.is_simulated,
                recommendation_reference=reference, error_code="SAFETY_REJECTED", error_message=reason,
            ))

        created_at = original.get("timestamp")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
        if isinstance(created_at, datetime):
            now = utc_now()
            if created_at.tzinfo is None:
                now = now.replace(tzinfo=None)
            age_seconds = (now - created_at).total_seconds()
            if age_seconds < 0 or age_seconds > self.recommendation_ttl_seconds:
                reason = f"Recommendation is stale; maximum age is {self.recommendation_ttl_seconds:g} seconds."
                EventTrace.log_event("CONTROL_VALIDATION", state.zone.zone_id, "control_service",
                                     {"command_id": command_id, "outcome": "REJECTED",
                                      "reason": "STALE_RECOMMENDATION"}, status="FAILED")
                return self._remember(ControlResult(
                    command_id=command_id, zone_id=state.zone.zone_id,
                    requested_setpoint=float(requested), previous_setpoint=float(state.hvac_status.present_value),
                    success=False, status="REJECTED", provider=self.provider.provider_identity,
                    simulated=self.provider.is_simulated, recommendation_reference=reference,
                    error_code="STALE_RECOMMENDATION", error_message=reason,
                ))

        # Revalidate against the fresh state immediately before the provider write.
        verified = self.safety.validate(validation.original_recommendation, state)
        if verified.outcome != "VALIDATED" or verified.validated_setpoint is None:
            reason = verified.rejection_reason or "Recommendation no longer passes safety validation against fresh state."
            EventTrace.log_event("CONTROL_VALIDATION", state.zone.zone_id, "control_service",
                                 {"command_id": command_id, "outcome": "REJECTED", "reason": reason}, status="FAILED")
            return self._remember(ControlResult(
                command_id=command_id, zone_id=state.zone.zone_id,
                requested_setpoint=float(requested), previous_setpoint=float(state.hvac_status.present_value),
                success=False, status="REJECTED", provider=self.provider.provider_identity,
                simulated=self.provider.is_simulated, recommendation_reference=reference,
                error_code="FRESH_STATE_REJECTED", error_message=reason,
            ))
        if float(requested) != verified.validated_setpoint:
            reason = "Validated setpoint changed before the control write."
            EventTrace.log_event("CONTROL_VALIDATION", state.zone.zone_id, "control_service",
                                 {"command_id": command_id, "outcome": "REJECTED", "reason": reason}, status="FAILED")
            return self._remember(ControlResult(
                command_id=command_id, zone_id=state.zone.zone_id,
                requested_setpoint=float(requested), previous_setpoint=float(state.hvac_status.present_value),
                success=False, status="REJECTED", provider=self.provider.provider_identity,
                simulated=self.provider.is_simulated, recommendation_reference=reference,
                error_code="SETPOINT_CHANGED", error_message=reason,
            ))

        try:
            provider_ready = bool(self.provider.is_ready)
        except Exception:
            provider_ready = False
        if not provider_ready:
            reason = "Building control provider is unavailable."
            EventTrace.log_event("CONTROL_VALIDATION", state.zone.zone_id, "control_service",
                                 {"command_id": command_id, "outcome": "REJECTED",
                                  "reason": "PROVIDER_UNAVAILABLE"}, status="FAILED")
            return self._remember(ControlResult(
                command_id=command_id, zone_id=state.zone.zone_id,
                requested_setpoint=float(requested), previous_setpoint=float(state.hvac_status.present_value),
                success=False, status="FAILED", provider=self.provider.provider_identity,
                simulated=self.provider.is_simulated, recommendation_reference=reference,
                error_code="PROVIDER_UNAVAILABLE", error_message=reason,
            ))

        EventTrace.log_event("CONTROL_VALIDATION", state.zone.zone_id, "control_service",
                             {"command_id": command_id, "outcome": "VALIDATED",
                              "validated_setpoint": verified.validated_setpoint})
        # Retain the legacy event for existing timeline consumers; the richer
        # lifecycle event above and provider acknowledgement events add detail.
        EventTrace.log_event("CONTROL_COMMAND", state.zone.zone_id, "control_service",
                             {"command_id": command_id, "setpoint": verified.validated_setpoint,
                              "provider": self.provider.provider_identity})
        command = HVACCommand(
            zone_id=state.zone.zone_id, setpoint=verified.validated_setpoint,
            source=verified.source, command_id=command_id,
            recommendation_reference=reference,
        )
        try:
            result = self.provider.write_command(command)
        except Exception:
            result = ControlResult(
                command_id=command_id, zone_id=state.zone.zone_id,
                requested_setpoint=verified.validated_setpoint,
                previous_setpoint=float(state.hvac_status.present_value),
                success=False, status="FAILED", provider=self.provider.provider_identity,
                simulated=self.provider.is_simulated, recommendation_reference=reference,
                error_code="PROVIDER_ERROR", error_message="Building control provider failed.",
            )
        return self._remember(result)

    def _remember(self, result: ControlResult) -> ControlResult:
        self.last_results[result.zone_id] = result
        return result

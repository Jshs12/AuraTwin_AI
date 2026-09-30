import math
import os
from datetime import datetime
from uuid import uuid4
from typing import Callable

from backend.core.events import EventTrace
from backend.core.interfaces import BuildingControlProvider
from backend.intelligence.schemas import SafetyValidationResult
from backend.safety.constraints import SafetyConstraintService
from backend.schemas.control import ControlResult, HVACCommand
from backend.schemas.state import ZoneState
from backend.core.time import utc_now
from backend.services.data_quality import DataQualityGate
from backend.services.control_state import ZoneControlStateService


class ControlService:
    """Authoritative safety gate and command lifecycle for building control."""

    def __init__(self, provider: BuildingControlProvider, safety: SafetyConstraintService | None = None,
                 data_quality: DataQualityGate | None = None,
                 state_provider: Callable[[str], ZoneState] | None = None,
                 control_states: ZoneControlStateService | None = None):
        self.provider = provider
        self.safety = safety or SafetyConstraintService()
        self.data_quality = data_quality or DataQualityGate()
        self.state_provider = state_provider
        self.control_states = control_states or ZoneControlStateService()
        self.last_results: dict[str, ControlResult] = {}
        try:
            self.recommendation_ttl_seconds = float(os.getenv("RECOMMENDATION_TTL_SECONDS", "60"))
            if not math.isfinite(self.recommendation_ttl_seconds) or self.recommendation_ttl_seconds <= 0:
                self.recommendation_ttl_seconds = 60.0
        except ValueError:
            self.recommendation_ttl_seconds = 60.0

    def apply_validated_recommendation(self, validation: SafetyValidationResult, state: ZoneState,
                                       *, current_state: ZoneState | None = None,
                                       final_state_provider: Callable[[str], ZoneState] | None = None) -> bool:
        """Backward-compatible boolean API; details live in the result method."""
        return self.apply_validated_recommendation_result(validation, state, current_state=current_state,
            final_state_provider=final_state_provider).success

    def apply_validated_recommendation_result(
        self, validation: SafetyValidationResult, state: ZoneState, *,
        current_state: ZoneState | None = None,
        final_state_provider: Callable[[str], ZoneState] | None = None,
    ) -> ControlResult:
        if current_state is not None:
            state = current_state
        elif self.state_provider is not None:
            try:
                state = self.state_provider(state.zone.zone_id)
            except Exception:
                return self._quality_failure(state, "CURRENT_STATE_UNAVAILABLE")
        quality = self.data_quality.assess_zone_state(state)
        state.data_quality = quality
        failures = self.data_quality.critical_failures(quality)
        if failures:
            reason_codes = sorted({item.reason_code or item.state.value for item in failures.values()})
            return self._quality_failure(state, ", ".join(reason_codes), failures)

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

        return self._execute_final_boundary(
            validation, state, requested=float(requested), command_id=command_id,
            reference=reference, final_state_provider=final_state_provider,
        )

    def _execute_final_boundary(self, validation: SafetyValidationResult, initial_state: ZoneState,
                                *, requested: float, command_id: str, reference: str | None,
                                final_state_provider: Callable[[str], ZoneState] | None) -> ControlResult:
        zone_id = initial_state.zone.zone_id
        with self.control_states.write_guard(zone_id):
            provider_ready = self._provider_ready()
            if provider_ready:
                self.control_states.note_provider_ready(zone_id)
            else:
                self.control_states.note_provider_unavailable(zone_id)
            blocked = self.control_states.blocked_reason(zone_id)
            if blocked:
                reason_code, event_type = blocked
                self.control_states.note_block(zone_id, command_id=command_id,
                                               reason_code=reason_code, event_type=event_type)
                return self._remember(ControlResult(
                    command_id=command_id, zone_id=zone_id, requested_setpoint=requested,
                    previous_setpoint=self._safe_current_setpoint(initial_state),
                    success=False, status="REJECTED", provider=self.provider.provider_identity,
                    simulated=self.provider.is_simulated, recommendation_reference=reference,
                    error_code=reason_code, error_message="Autonomous control is blocked by the current zone control mode.",
                ))
            if not provider_ready:
                self.control_states.note_provider_failure(zone_id, command_id=command_id,
                    reason_code="PROVIDER_UNAVAILABLE", provider_ready=False)
                return self._remember(ControlResult(
                    command_id=command_id, zone_id=zone_id, requested_setpoint=requested,
                    previous_setpoint=self._safe_current_setpoint(initial_state),
                    success=False, status="FAILED", provider=self.provider.provider_identity,
                    simulated=self.provider.is_simulated, recommendation_reference=reference,
                    error_code="PROVIDER_UNAVAILABLE", error_message="Building control provider is unavailable.",
                ))

            # Re-read and re-run every safety gate while holding the same zone
            # lock that serializes mode changes with the provider write.
            state_provider = final_state_provider or self.state_provider
            if state_provider is not None:
                try:
                    state = state_provider(zone_id)
                except Exception:
                    return self._quality_failure(initial_state, "CURRENT_STATE_UNAVAILABLE")
            else:
                state = initial_state

            quality = self.data_quality.assess_zone_state(state)
            state.data_quality = quality
            failures = self.data_quality.critical_failures(quality)
            if failures:
                codes = sorted({item.reason_code or item.state.value for item in failures.values()})
                return self._quality_failure(state, ", ".join(codes), failures)

            created_at = (validation.original_recommendation or {}).get("timestamp")
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
                    return self._validation_failure(state, command_id, reference, requested,
                        "STALE_RECOMMENDATION", "Recommendation expired before the final provider boundary.")

            checked = self.safety.validate(validation.original_recommendation, state)
            if checked.outcome != "VALIDATED" or checked.validated_setpoint is None:
                return self._validation_failure(state, command_id, reference, requested,
                    "FRESH_STATE_REJECTED", checked.rejection_reason or "Recommendation failed final safety validation.")
            if checked.validated_setpoint != requested:
                return self._validation_failure(state, command_id, reference, requested,
                    "SETPOINT_CHANGED", "Validated setpoint changed before the provider write.")

            command = HVACCommand(zone_id=zone_id, setpoint=checked.validated_setpoint,
                source=checked.source, command_id=command_id, recommendation_reference=reference)
            command_check = self.safety.validate_command(command, state)
            if command_check.outcome != "VALIDATED":
                return self._validation_failure(state, command_id, reference, requested,
                    "COMMAND_LIMIT_REJECTED", command_check.rejection_reason or "Command limit validation rejected the command.")

            # Recheck readiness and both mode flags at the last boundary. The
            # held lock ensures no operator state update can slip between this
            # check and write_command.
            if not self._provider_ready():
                self.control_states.note_provider_failure(zone_id, command_id=command_id,
                    reason_code="PROVIDER_UNAVAILABLE", provider_ready=False)
                return self._remember(ControlResult(
                    command_id=command_id, zone_id=zone_id, requested_setpoint=requested,
                    previous_setpoint=self._safe_current_setpoint(state), success=False,
                    status="FAILED", provider=self.provider.provider_identity,
                    simulated=self.provider.is_simulated, recommendation_reference=reference,
                    error_code="PROVIDER_UNAVAILABLE", error_message="Building control provider became unavailable.",
                ))
            blocked = self.control_states.blocked_reason(zone_id)
            if blocked:
                reason_code, event_type = blocked
                self.control_states.note_block(zone_id, command_id=command_id,
                                               reason_code=reason_code, event_type=event_type)
                return self._remember(ControlResult(
                    command_id=command_id, zone_id=zone_id, requested_setpoint=requested,
                    previous_setpoint=self._safe_current_setpoint(state), success=False,
                    status="REJECTED", provider=self.provider.provider_identity,
                    simulated=self.provider.is_simulated, recommendation_reference=reference,
                    error_code=reason_code, error_message="Autonomous control is blocked at the final provider boundary.",
                ))

            EventTrace.log_event("CONTROL_VALIDATION", zone_id, "control_service",
                                 {"command_id": command_id, "outcome": "VALIDATED",
                                  "validated_setpoint": checked.validated_setpoint})
            EventTrace.log_event("CONTROL_COMMAND", zone_id, "control_service",
                                 {"command_id": command_id, "setpoint": checked.validated_setpoint,
                                  "provider": self.provider.provider_identity})
            try:
                result = self.provider.write_command(command)
            except Exception:
                result = ControlResult(command_id=command_id, zone_id=zone_id,
                    requested_setpoint=checked.validated_setpoint,
                    previous_setpoint=self._safe_current_setpoint(state), success=False,
                    status="FAILED", provider=self.provider.provider_identity,
                    simulated=self.provider.is_simulated, recommendation_reference=reference,
                    error_code="PROVIDER_ERROR", error_message="Building control provider failed.")

            if not isinstance(result, ControlResult):
                result = ControlResult(command_id=command_id, zone_id=zone_id,
                    requested_setpoint=checked.validated_setpoint,
                    previous_setpoint=self._safe_current_setpoint(state), success=False,
                    status="FAILED", provider=self.provider.provider_identity,
                    simulated=self.provider.is_simulated, recommendation_reference=reference,
                    error_code="INVALID_PROVIDER_RESULT", error_message="Provider returned an invalid command result.")
            if not result.success or result.status != "SUCCESS":
                if result.success or result.status == "SUCCESS":
                    result = result.model_copy(update={"success": False, "status": "FAILED",
                        "applied_setpoint": None, "error_code": "INCONSISTENT_PROVIDER_RESULT",
                        "error_message": "Provider returned an inconsistent command result."})
                self.control_states.note_provider_failure(zone_id, command_id=command_id,
                    reason_code=result.error_code or "PROVIDER_WRITE_FAILED", provider_ready=True)
                return self._remember(result)

            self.control_states.note_command_success(zone_id, command_id=command_id,
                                                     simulated=self.provider.is_simulated)
            return self._remember(result)

    def _provider_ready(self) -> bool:
        try:
            return bool(self.provider.is_ready)
        except Exception:
            return False

    @staticmethod
    def _safe_current_setpoint(state: ZoneState) -> float | None:
        value = state.hvac_status.present_value
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None

    def _validation_failure(self, state: ZoneState, command_id: str, reference: str | None,
                            requested: float, error_code: str, reason: str) -> ControlResult:
        EventTrace.log_event("CONTROL_VALIDATION", state.zone.zone_id, "control_service",
                             {"command_id": command_id, "outcome": "REJECTED",
                              "reason": error_code}, status="FAILED")
        return self._remember(ControlResult(command_id=command_id, zone_id=state.zone.zone_id,
            requested_setpoint=requested, previous_setpoint=self._safe_current_setpoint(state),
            success=False, status="REJECTED", provider=self.provider.provider_identity,
            simulated=self.provider.is_simulated, recommendation_reference=reference,
            error_code=error_code, error_message=reason))

    def _quality_failure(self, state: ZoneState, reason: str, failures: dict | None = None) -> ControlResult:
        command_id = str(uuid4())
        EventTrace.log_event("CONTROL_VALIDATION", state.zone.zone_id, "data_quality_gate",
            {"command_id": command_id, "outcome": "REJECTED", "reason": reason,
             "signals": {name: item.model_dump(mode="json") for name, item in (failures or {}).items()}},
            status="FAILED")
        return self._remember(ControlResult(command_id=command_id, zone_id=state.zone.zone_id,
            requested_setpoint=None, previous_setpoint=(float(state.hvac_status.present_value)
                if isinstance(state.hvac_status.present_value, (int, float))
                and math.isfinite(float(state.hvac_status.present_value)) else None),
            success=False, status="REJECTED", provider=self.provider.provider_identity,
            simulated=self.provider.is_simulated, error_code="DATA_QUALITY_REJECTED",
            error_message=f"Control blocked by data quality gate ({reason})."))

    def _remember(self, result: ControlResult) -> ControlResult:
        self.last_results[result.zone_id] = result
        return result

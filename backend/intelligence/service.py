from typing import Any, Optional

from backend.core.events import EventTrace
from backend.intelligence.providers import IntelligenceProvider, MockIntelligenceProvider
from backend.intelligence.schemas import (
    IntelligenceContext, IntelligenceRecommendation, RecommendationDecision, SafetyValidationResult,
)
from backend.optimization.engine import OptimizationEngine
from backend.safety.constraints import SafetyConstraintService
from backend.services.data_quality import DataQualityGate
from backend.schemas.state import ZoneState


class RecommendationWorkflow:
    def __init__(self, provider: Optional[IntelligenceProvider] = None,
                 optimizer: Optional[OptimizationEngine] = None,
                 safety: Optional[SafetyConstraintService] = None,
                 data_quality: Optional[DataQualityGate] = None):
        self.provider = provider or MockIntelligenceProvider()
        self.optimizer = optimizer or OptimizationEngine()
        self.safety = safety or SafetyConstraintService()
        self.data_quality = data_quality or DataQualityGate()

    def build_context(self, state: ZoneState) -> IntelligenceContext:
        return IntelligenceContext(
            zone_id=state.zone.zone_id, zone_type=state.zone.type,
            occupancy_count=state.occupancy.people_count,
            occupancy_percentage=state.occupancy.occupancy_percentage,
            occupancy_level=state.occupancy.occupancy_state,
            current_temperature=state.temperature,
            comfort_min_temperature=state.zone.comfort.min_temperature,
            comfort_max_temperature=state.zone.comfort.max_temperature,
            current_hvac_setpoint=float(state.hvac_status.present_value),
            current_power_kw=state.energy.power_kw,
            energy_kwh=state.energy.energy_kwh, energy_cost=state.energy.cost,
            tariff_rate_per_kwh=state.tariff.rate_per_kwh,
            tariff_currency=state.tariff.currency,
            provenance={
                "occupancy": "occupancy_provider_output" if state.occupancy.occupancy_state != "UNKNOWN" else "unavailable",
                "temperature": "mock_temperature_provider",
                "current_hvac_setpoint": "mock_building_control_provider",
                "energy": "simulated" if state.energy.is_simulated else "provider",
                "tariff": "mock_tariff_provider",
                "zone_type": "building_zone_configuration",
                "comfort_limits": "building_zone_configuration",
                "timestamp": "system_clock_utc",
                "occupancy_percentage": "derived_from_detection_and_capacity",
            },
            # No persisted history or prior-recommendation store exists yet.
            historical_context=None, previous_recommendation=None,
        )

    def recommend(self, state: ZoneState) -> RecommendationDecision:
        quality = self.data_quality.assess_zone_state(state)
        state.data_quality = quality
        failures = self.data_quality.critical_failures(quality)
        if failures:
            return self._quality_rejected(state, failures)
        context = self.build_context(state)
        EventTrace.log_event("INTELLIGENCE_REQUESTED", state.zone.zone_id, "recommendation_workflow", context.model_dump(mode="json"))
        try:
            candidate = self.provider.generate_recommendation(context)
            if not isinstance(candidate, IntelligenceRecommendation):
                candidate = IntelligenceRecommendation.model_validate(candidate)
            EventTrace.log_event("INTELLIGENCE_RESPONSE", state.zone.zone_id, "recommendation_workflow", {
                "zone_id": candidate.zone_id,
                "action_type": candidate.action_type,
            })
            validation = self.safety.validate(candidate, state)
        except Exception as exc:
            candidate = None
            # Keep exception text out of events: transport errors may include sensitive details.
            reason = f"Intelligence provider unavailable or returned malformed data ({type(exc).__name__})."
            EventTrace.log_event("INTELLIGENCE_RESPONSE", state.zone.zone_id, "recommendation_workflow", {"error": reason}, status="FAILED")
            validation = self.safety.validate(None, state)
            validation.rejection_reason = reason

        if validation.outcome == "VALIDATED":
            self._log_validation(state.zone.zone_id, validation)
            return RecommendationDecision(zone_id=state.zone.zone_id, intelligence_recommendation=candidate,
                                          validation=validation, recommendation_kind="intelligence")

        EventTrace.log_event("RECOMMENDATION_REJECTED", state.zone.zone_id, "safety_constraint_service",
                             {"reason": validation.rejection_reason}, status="FAILED")
        reason = validation.rejection_reason or "Intelligence recommendation unavailable."
        return self._fallback(state, candidate, reason)

    def validate_submitted(self, state: ZoneState, recommendation: Any) -> RecommendationDecision:
        """Revalidate client-submitted advisory data; never trust client validation fields."""
        quality = self.data_quality.assess_zone_state(state)
        state.data_quality = quality
        failures = self.data_quality.critical_failures(quality)
        if failures:
            return self._quality_rejected(state, failures)
        validation = self.safety.validate(recommendation, state)
        if validation.outcome == "VALIDATED":
            self._log_validation(state.zone.zone_id, validation)
            candidate = IntelligenceRecommendation.model_validate(recommendation)
            return RecommendationDecision(zone_id=state.zone.zone_id, intelligence_recommendation=candidate,
                                          validation=validation, recommendation_kind="intelligence")
        EventTrace.log_event("RECOMMENDATION_REJECTED", state.zone.zone_id, "safety_constraint_service",
                             {"reason": validation.rejection_reason}, status="FAILED")
        return self._fallback(state, None, validation.rejection_reason or "Submitted recommendation rejected.")

    def _fallback(self, state: ZoneState, candidate: Optional[IntelligenceRecommendation], reason: str) -> RecommendationDecision:
        # Fallback uses the same state inputs and must independently pass the
        # critical input quality policy before the optimizer is invoked.
        quality = self.data_quality.assess_zone_state(state)
        state.data_quality = quality
        failures = self.data_quality.critical_failures(quality)
        if failures:
            return self._quality_rejected(state, failures, candidate=candidate)
        deterministic = self.optimizer.generate_recommendation(state)
        # Adapt deterministic optimizer output into the same advisory contract.
        fallback_candidate = IntelligenceRecommendation(
            zone_id=deterministic.zone_id,
            recommended_setpoint=deterministic.recommended_setpoint,
            rationale=deterministic.reason, confidence=1.0,
            provider="deterministic_optimizer", model_source=deterministic.source,
            context_reference={"schema_version": "1.0", "zone_id": deterministic.zone_id},
        )
        validation = self.safety.validate_fallback(fallback_candidate, state, reason)
        EventTrace.log_event("FALLBACK_ACTIVATED", state.zone.zone_id, "deterministic_optimizer",
                             {"reason": reason, "recommended_setpoint": deterministic.recommended_setpoint,
                              "validation_outcome": validation.outcome})
        self._log_validation(state.zone.zone_id, validation)
        return RecommendationDecision(
            zone_id=state.zone.zone_id,
            intelligence_recommendation=candidate,
            deterministic_recommendation=deterministic.model_dump(mode="json"),
            validation=validation,
            recommendation_kind="deterministic_fallback" if validation.outcome == "FALLBACK" else "rejected",
        )

    @staticmethod
    def _quality_rejected(state: ZoneState, failures: dict,
                          candidate: Optional[IntelligenceRecommendation] = None) -> RecommendationDecision:
        safe_codes = sorted({assessment.reason_code or assessment.state.value
                             for assessment in failures.values()})
        reason = "Critical input quality check failed: " + ", ".join(safe_codes)
        EventTrace.log_event("DATA_QUALITY_REJECTED", state.zone.zone_id, "data_quality_gate",
                             {name: assessment.model_dump(mode="json")
                              for name, assessment in failures.items()}, status="FAILED")
        validation = SafetyValidationResult(outcome="REJECTED", original_recommendation=None,
            rejection_reason=reason, source="data_quality_gate")
        EventTrace.log_event("RECOMMENDATION_REJECTED", state.zone.zone_id, "data_quality_gate",
                             {"reason": reason}, status="FAILED")
        return RecommendationDecision(zone_id=state.zone.zone_id,
            intelligence_recommendation=candidate, validation=validation,
            recommendation_kind="rejected")

    @staticmethod
    def _log_validation(zone_id: str, validation: Any) -> None:
        event_type = "RECOMMENDATION_VALIDATED" if validation.outcome in {"VALIDATED", "FALLBACK"} else "RECOMMENDATION_REJECTED"
        EventTrace.log_event(event_type, zone_id, "safety_constraint_service", validation.model_dump(mode="json"),
                             status="SUCCESS" if validation.outcome in {"VALIDATED", "FALLBACK"} else "FAILED")

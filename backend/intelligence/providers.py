from abc import ABC, abstractmethod

from backend.intelligence.schemas import IntelligenceContext, IntelligenceRecommendation


class IntelligenceProvider(ABC):
    provider_name = "intelligence_provider"

    @abstractmethod
    def generate_recommendation(self, context: IntelligenceContext) -> IntelligenceRecommendation:
        """Return advisory data only; providers cannot access HVAC control services."""


class MockIntelligenceProvider(IntelligenceProvider):
    provider_name = "mock_intelligence_provider"

    def generate_recommendation(self, context: IntelligenceContext) -> IntelligenceRecommendation:
        # A stable, comfort-preserving advisory used to exercise the provider contract.
        setpoint = (context.comfort_min_temperature + context.comfort_max_temperature) / 2.0
        if context.occupancy_level == "HIGH":
            setpoint = context.comfort_min_temperature + 1.0
        return IntelligenceRecommendation(
            zone_id=context.zone_id,
            recommended_setpoint=setpoint,
            rationale="Mock advisory keeps the setpoint within the configured comfort band.",
            confidence=0.85,
            provider=self.provider_name,
            model_source="mock_rules_v1",
            context_reference={"schema_version": context.schema_version, "zone_id": context.zone_id},
            action_type="setpoint_adjustment",
        )

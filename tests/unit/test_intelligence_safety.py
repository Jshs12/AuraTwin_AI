import asyncio
from datetime import datetime

import pytest

from backend.core.mock_providers import (
    MockBuildingControlProvider, MockEnergyProvider, MockOccupancyProvider,
    MockTariffProvider, MockTemperatureProvider,
)
from backend.core.events import EventTrace
from backend.core.monitoring import ZoneMonitoringScheduler
from backend.intelligence.providers import MockIntelligenceProvider
from backend.intelligence.schemas import IntelligenceRecommendation, SafetyValidationResult
from backend.intelligence.service import RecommendationWorkflow
from backend.optimization.engine import OptimizationEngine
from backend.safety.constraints import SafetyConstraintService
from backend.schemas.events import OccupancyEvent
from backend.schemas.state import ZoneState
from backend.schemas.zone import ComfortLimits, Zone
from backend.services.control import ControlService
from backend.services.zone_state import ZoneStateService
from backend.core.time import utc_now


def make_state(occupancy="MEDIUM", setpoint=24.0):
    zone = Zone(zone_id="test_zone", name="Test", type="classroom", capacity=40,
                area_m2=60, comfort=ComfortLimits(min_temperature=22, max_temperature=26))
    occ = OccupancyEvent(zone_id="test_zone", people_count=20, capacity=40,
                         occupancy_percentage=50, occupancy_state=occupancy,
                         observed_at=utc_now(), source="test_simulation", simulated=True)
    tariffs = MockTariffProvider()
    energy = MockEnergyProvider(tariffs).get_energy("test_zone")
    from backend.schemas.control import BACnetReadResult
    return ZoneState(zone=zone, occupancy=occ, temperature=24, energy=energy,
                     tariff=energy.tariff,
                     hvac_status=BACnetReadResult(zone_id="test_zone", object_id="AV:1", present_value=setpoint,
                         timestamp=utc_now(), observed_at=utc_now(), simulated=True),
                     occupancy_source="mock_occupancy_provider", temperature_source="mock_temperature_provider",
                     temperature_observed_at=utc_now(), temperature_simulated=True)


def advisory(state, setpoint=24.0, confidence=0.9, **overrides):
    values = dict(zone_id=state.zone.zone_id, recommended_setpoint=setpoint,
                  rationale="test advisory", confidence=confidence, provider="test_provider",
                  model_source="unit_test", context_reference={"zone_id": state.zone.zone_id},
                  action_type="setpoint_adjustment")
    values.update(overrides)
    return IntelligenceRecommendation(**values)


def test_valid_intelligence_recommendation_is_validated():
    state = make_state()
    result = SafetyConstraintService().validate(advisory(state), state)
    assert result.outcome == "VALIDATED"
    assert result.validated_setpoint == 24.0


@pytest.mark.parametrize("raw", [None, "bad", {"zone_id": "test_zone"}])
def test_malformed_recommendation_is_rejected(raw):
    assert SafetyConstraintService().validate(raw, make_state()).outcome == "REJECTED"


@pytest.mark.parametrize("setpoint", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_infinite_setpoints_are_rejected(setpoint):
    assert SafetyConstraintService().validate(advisory(make_state(), setpoint), make_state()).outcome == "REJECTED"


def test_hard_bounds_are_enforced():
    result = SafetyConstraintService().validate(advisory(make_state(), 31), make_state())
    assert result.outcome == "REJECTED"
    assert "configured bounds" in result.rejection_reason


def test_zone_comfort_limits_are_enforced():
    result = SafetyConstraintService().validate(advisory(make_state(), 27), make_state())
    assert result.outcome == "REJECTED"
    assert "comfort limits" in result.rejection_reason


def test_maximum_setpoint_change_is_enforced():
    result = SafetyConstraintService().validate(advisory(make_state(setpoint=23), 26), make_state(setpoint=23))
    assert result.outcome == "REJECTED"
    assert "change exceeds" in result.rejection_reason


def test_unavailable_provider_activates_validated_deterministic_fallback():
    class Offline:
        def generate_recommendation(self, context):
            raise RuntimeError("offline")
    decision = RecommendationWorkflow(provider=Offline()).recommend(make_state())
    assert decision.recommendation_kind == "deterministic_fallback"
    assert decision.validation.outcome == "FALLBACK"
    assert decision.validation.validated_setpoint == 24.0


def test_low_confidence_uses_deterministic_fallback():
    class LowConfidence:
        def generate_recommendation(self, context):
            return advisory(make_state(), confidence=0.2)
    decision = RecommendationWorkflow(provider=LowConfidence()).recommend(make_state())
    assert decision.validation.outcome == "FALLBACK"
    assert "Confidence" in decision.validation.fallback_reason


def test_out_of_comfort_fallback_is_rejected_and_never_controls():
    state = make_state(occupancy="EMPTY")
    class UnsafeProvider:
        def generate_recommendation(self, context):
            return advisory(state, 27)
    decision = RecommendationWorkflow(provider=UnsafeProvider()).recommend(state)
    assert decision.recommendation_kind == "rejected"
    assert decision.validation.outcome == "REJECTED"
    provider = MockBuildingControlProvider()
    assert ControlService(provider).apply_validated_recommendation(decision.validation, state) is False
    assert provider.read_status("test_zone").present_value == 24.0


def test_validated_recommendation_reaches_control_provider():
    state = make_state(setpoint=23)
    validation = SafetyConstraintService().validate(advisory(state, 25), state)
    provider = MockBuildingControlProvider()
    assert ControlService(provider).apply_validated_recommendation(validation, state) is True
    assert provider.read_status("test_zone").present_value == 25


def test_intelligence_event_sequence_includes_response_validation_and_fallback():
    EventTrace.clear()
    decision = RecommendationWorkflow().recommend(make_state())
    types = [event.event_type for event in EventTrace.get_history("test_zone")]
    assert types.index("INTELLIGENCE_REQUESTED") < types.index("INTELLIGENCE_RESPONSE")
    assert "RECOMMENDATION_VALIDATED" in types
    assert decision.validation.outcome == "VALIDATED"


def test_monitoring_path_uses_recommendation_workflow_and_validator():
    state = make_state(setpoint=23)
    zone_state_service = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
                                         MockEnergyProvider(MockTariffProvider()), MockBuildingControlProvider())
    zone_state_service._zones["classroom_01"] = state.zone
    zone_state_service.get_zone_state = lambda zone_id: state

    class Detector:
        def detect_from_image(self, frame, zone_id, capacity):
            return state.occupancy, {}
    class ControlSpy:
        def __init__(self): self.seen = []
        def apply_validated_recommendation(self, validation, state): self.seen.append((validation, state)); return True
    control = ControlSpy()
    scheduler = ZoneMonitoringScheduler(zone_state_service, RecommendationWorkflow(MockIntelligenceProvider()), control, Detector())
    scheduler.camera.get_frame = lambda zone_id: b"frame"
    asyncio.run(scheduler._process_zone("classroom_01"))
    assert len(control.seen) == 1
    assert control.seen[0][0].outcome == "VALIDATED"
    assert control.seen[0][1].zone.zone_id == "test_zone"


def test_camera_capture_runs_off_the_async_event_loop():
    import time
    state = make_state()
    zone_state_service = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
                                         MockEnergyProvider(MockTariffProvider()), MockBuildingControlProvider())
    zone_state_service._zones["classroom_01"] = state.zone
    zone_state_service.get_zone_state = lambda zone_id: state

    class Detector:
        def detect_from_image(self, frame, zone_id, capacity):
            return state.occupancy, {}
    class ControlSpy:
        def apply_validated_recommendation(self, validation, state): return True

    scheduler = ZoneMonitoringScheduler(zone_state_service, RecommendationWorkflow(MockIntelligenceProvider()),
                                         ControlSpy(), Detector())
    def slow_capture(zone_id):
        time.sleep(0.15)
        return b"frame"
    scheduler.camera.get_frame = slow_capture

    async def verify_loop_responsive():
        capture = asyncio.create_task(scheduler._process_zone("classroom_01"))
        await asyncio.sleep(0.01)
        responsive_before_capture_finishes = not capture.done()
        await capture
        return responsive_before_capture_finishes

    assert asyncio.run(verify_loop_responsive())


def test_monitoring_intervals_and_unknown_camera_provider_use_safe_defaults(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("INFERENCE_COOLDOWN_SECONDS", "-10")
    monkeypatch.setenv("SCENE_CHANGE_THRESHOLD", "nan")
    monkeypatch.setenv("CAMERA_PROVIDER", "unknown-camera")
    service = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
                               MockEnergyProvider(MockTariffProvider()), MockBuildingControlProvider())
    scheduler = ZoneMonitoringScheduler(service, RecommendationWorkflow(),
                                         ControlService(MockBuildingControlProvider()), object())
    assert scheduler.snapshot_interval == 1
    assert scheduler.inference_cooldown == 0
    assert scheduler.scene_detector.threshold == 50.0
    assert scheduler.camera_provider == "mock"


def test_intelligence_context_marks_simulation_and_omits_unavailable_history():
    context = RecommendationWorkflow().build_context(make_state())
    assert context.schema_version == "1.0"
    assert context.provenance["energy"] == "simulated"
    assert context.historical_context is None
    assert context.previous_recommendation is None

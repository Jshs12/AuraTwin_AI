from datetime import datetime, timedelta, timezone

import pytest

from backend.core.events import EventTrace
from backend.core.mock_providers import MockEnergyProvider, MockOccupancyProvider, MockTariffProvider, MockTemperatureProvider
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider
from backend.intelligence.schemas import IntelligenceRecommendation
from backend.intelligence.providers import MockIntelligenceProvider
from backend.intelligence.service import RecommendationWorkflow
from backend.safety.constraints import SafetyConstraintService
from backend.schemas.control import BACnetReadResult, HVACCommand
from backend.schemas.energy import EnergyReading, Tariff
from backend.schemas.events import OccupancyEvent
from backend.schemas.state import ZoneState
from backend.schemas.zone import ComfortLimits, Zone
from backend.services.control import ControlService
from backend.services.zone_state import ZoneStateService
from backend.core.time import utc_now


def make_state(setpoint: float = 24.0) -> ZoneState:
    tariff = Tariff(tariff_id="test", rate_per_kwh=0.15)
    energy = EnergyReading(zone_id="test_zone", power_kw=4.8, energy_kwh=100.5,
                           cost=15.075, tariff=tariff, is_simulated=True)
    return ZoneState(
        zone=Zone(zone_id="test_zone", name="Test", type="classroom", capacity=30,
                  area_m2=50, comfort=ComfortLimits(min_temperature=22, max_temperature=26)),
        occupancy=OccupancyEvent(zone_id="test_zone", people_count=12, capacity=30,
                                 occupancy_percentage=40, occupancy_state="MEDIUM",
                                 observed_at=utc_now(), source="test_simulation", simulated=True),
        temperature=27.1, energy=energy, tariff=tariff,
        hvac_status=BACnetReadResult(zone_id="test_zone", object_id="SIMULATED_POINT:cooling_setpoint",
                                     present_value=setpoint, provider="SIMULATED BACNET",
                                     timestamp=utc_now(), observed_at=utc_now(), simulated=True),
        occupancy_source="test_simulation", temperature_source="test_simulation",
        temperature_observed_at=utc_now(), temperature_simulated=True,
    )


def recommendation(state: ZoneState, setpoint: float = 25.0) -> IntelligenceRecommendation:
    return IntelligenceRecommendation(
        zone_id=state.zone.zone_id, recommended_setpoint=setpoint,
        rationale="Test advisory", confidence=0.9, provider="mock_intelligence_provider",
        model_source="unit_test", context_reference={"zone_id": state.zone.zone_id},
        action_type="setpoint_adjustment",
    )


def test_simulated_provider_success_acknowledges_and_emits_telemetry():
    EventTrace.clear()
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"])
    result = provider.write_command(HVACCommand(zone_id="test_zone", setpoint=25, source="test"))
    assert result.success is True
    assert result.status == "SUCCESS"
    assert result.provider == "SIMULATED BACNET"
    assert result.previous_setpoint == 24
    assert result.applied_setpoint == 25
    assert result.hvac_mode == "COOLING"
    assert result.power_kw > 0
    events = [event.event_type for event in EventTrace.get_history("test_zone")]
    assert events == ["CONTROL_COMMAND_SENT", "CONTROL_ACKNOWLEDGED", "HVAC_RESPONSE", "ENERGY_UPDATE"]


@pytest.mark.parametrize("setpoint", [15.9, 30.1, float("nan")])
def test_simulated_provider_rejects_invalid_setpoints(setpoint):
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"])
    result = provider.write_command(HVACCommand(zone_id="test_zone", setpoint=setpoint, source="test"))
    assert result.success is False
    assert result.status == "REJECTED"
    assert provider.read_status("test_zone").present_value == 24.0


def test_simulated_provider_rejects_unknown_zone():
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["known_zone"])
    result = provider.write_command(HVACCommand(zone_id="unknown_zone", setpoint=25, source="test"))
    assert result.status == "REJECTED"
    assert result.error_code == "UNKNOWN_ZONE"


@pytest.mark.parametrize(
    ("failure_mode", "error_code", "expected_events"),
    [
        ("unavailable", "PROVIDER_UNAVAILABLE", ["CONTROL_COMMAND_SENT"]),
        ("acknowledgement", "ACKNOWLEDGEMENT_FAILED", ["CONTROL_COMMAND_SENT", "CONTROL_ACKNOWLEDGED"]),
        ("hvac_response", "HVAC_RESPONSE_FAILED", ["CONTROL_COMMAND_SENT", "CONTROL_ACKNOWLEDGED", "HVAC_RESPONSE"]),
    ],
)
def test_simulated_provider_failure_modes_are_not_reported_as_success(failure_mode, error_code, expected_events):
    EventTrace.clear()
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"], failure_mode=failure_mode)
    result = provider.write_command(HVACCommand(zone_id="test_zone", setpoint=25, source="test"))
    assert result.success is False
    assert result.status == "FAILED"
    assert result.error_code == error_code
    assert [event.event_type for event in EventTrace.get_history("test_zone")] == expected_events


def test_control_service_validates_before_provider_and_returns_structured_result():
    EventTrace.clear()
    state = make_state(setpoint=23)
    candidate = recommendation(state, 25)
    validation = SafetyConstraintService().validate(candidate, state)
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"])
    result = ControlService(provider).apply_validated_recommendation_result(validation, state)
    assert result.success is True
    assert result.requested_setpoint == 25
    assert result.applied_setpoint == 25
    assert result.energy_kwh is not None
    types = [event.event_type for event in EventTrace.get_history("test_zone")]
    assert types.index("CONTROL_COMMAND_REQUESTED") < types.index("CONTROL_VALIDATION")
    assert types.index("CONTROL_VALIDATION") < types.index("CONTROL_COMMAND_SENT")
    assert types[types.index("CONTROL_COMMAND_REQUESTED"):] == [
        "CONTROL_COMMAND_REQUESTED", "CONTROL_VALIDATION", "CONTROL_VALIDATION",
        "CONTROL_COMMAND", "CONTROL_COMMAND_SENT", "CONTROL_ACKNOWLEDGED",
        "HVAC_RESPONSE", "ENERGY_UPDATE",
    ]


def test_hvac_response_updates_zone_temperature_and_energy_reading():
    state = make_state(setpoint=24)
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"])
    state_service = ZoneStateService(
        MockOccupancyProvider(), MockTemperatureProvider(),
        MockEnergyProvider(MockTariffProvider()), provider,
    )
    state_service._zones["test_zone"] = state.zone
    before = state_service.get_zone_state("test_zone")
    validation = SafetyConstraintService().validate(recommendation(before, 25), before)
    result = ControlService(provider).apply_validated_recommendation_result(validation, before)
    after = state_service.get_zone_state("test_zone")
    assert result.success is True
    assert after.temperature == result.current_temperature
    assert after.energy.power_kw == result.power_kw
    assert after.energy.energy_kwh == result.energy_kwh
    assert after.energy.energy_kwh > before.energy.energy_kwh


def test_rejected_safety_result_never_reaches_provider():
    EventTrace.clear()
    state = make_state()
    validation = SafetyConstraintService().validate(recommendation(state, 27), state)
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"])
    result = ControlService(provider).apply_validated_recommendation_result(validation, state)
    assert result.status == "REJECTED"
    assert provider.read_status("test_zone").present_value == 24
    assert not any(e.event_type == "CONTROL_COMMAND_SENT" for e in EventTrace.get_history("test_zone"))


def test_unready_provider_is_not_called_after_safety_validation():
    EventTrace.clear()
    state = make_state(setpoint=23)
    validation = SafetyConstraintService().validate(recommendation(state, 25), state)
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"], failure_mode="unavailable")
    result = ControlService(provider).apply_validated_recommendation_result(validation, state)
    assert result.status == "FAILED"
    assert result.error_code == "PROVIDER_UNAVAILABLE"
    assert provider.read_status("test_zone").present_value == 24.0
    assert not any(e.event_type == "CONTROL_COMMAND_SENT" for e in EventTrace.get_history("test_zone"))


def test_recommendation_validation_does_not_echo_untrusted_input():
    state = make_state()
    marker = "private-value-must-not-be-echoed"
    raw = {
        "zone_id": state.zone.zone_id,
        "recommended_setpoint": 24,
        "rationale": marker,
        "confidence": 0.9,
        "provider": marker,
        "model_source": marker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "context_reference": {"zone_id": state.zone.zone_id, "token": marker},
        "extra_secret": marker,
    }
    result = SafetyConstraintService().validate(raw, state)
    assert result.outcome == "VALIDATED"
    assert result.original_recommendation is not None
    assert marker not in str(result.original_recommendation)
    assert marker not in (result.rejection_reason or "")


def test_fresh_state_revalidation_rejects_stale_setpoint_before_write():
    original_state = make_state(setpoint=23)
    validation = SafetyConstraintService().validate(recommendation(original_state, 25), original_state)
    changed_state = original_state.model_copy(update={
        "hvac_status": original_state.hvac_status.model_copy(update={"present_value": 28.0})
    })
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"])
    result = ControlService(provider).apply_validated_recommendation_result(validation, changed_state)
    assert result.status == "REJECTED"
    assert result.error_code == "FRESH_STATE_REJECTED"
    assert provider.read_status("test_zone").present_value == 24.0


def test_expired_recommendation_is_rejected_before_write():
    state = make_state(setpoint=23)
    expired = recommendation(state, 25).model_copy(update={
        "timestamp": datetime.now(timezone.utc) - timedelta(seconds=61),
    })
    validation = SafetyConstraintService().validate(expired, state)
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"])
    result = ControlService(provider).apply_validated_recommendation_result(validation, state)
    assert result.status == "REJECTED"
    assert result.error_code == "STALE_RECOMMENDATION"
    assert provider.read_status("test_zone").present_value == 24.0


def test_simulated_points_are_semantic_and_do_not_claim_object_instance_numbers():
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["test_zone"])
    points = {point.point_name: point for point in provider.get_points("test_zone")}
    assert points["temperature_present_value"].writable is False
    assert points["cooling_setpoint"].writable is True
    assert points["hvac_mode"].present_value == "COOLING"
    assert all("AV:" not in point.point_name for point in points.values())


def test_intelligence_provider_has_no_building_control_capability():
    assert not hasattr(MockIntelligenceProvider(), "write_setpoint")
    assert "control_service" not in RecommendationWorkflow.__init__.__annotations__

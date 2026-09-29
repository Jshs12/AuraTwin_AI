import asyncio
from datetime import timedelta

import pytest

from backend.core.events import EventTrace
from backend.core.monitoring import ZoneMonitoringScheduler
from backend.core.mock_providers import (
    MockBuildingControlProvider, MockEnergyProvider, MockOccupancyProvider,
    MockTariffProvider, MockTemperatureProvider,
)
from backend.core.time import utc_now
from backend.intelligence.providers import MockIntelligenceProvider
from backend.intelligence.schemas import IntelligenceContext, IntelligenceRecommendation
from backend.intelligence.service import RecommendationWorkflow
from backend.schemas.control import BACnetReadResult
from backend.schemas.data_quality import QualityState
from backend.schemas.energy import EnergyReading
from backend.schemas.events import OccupancyEvent
from backend.schemas.state import ZoneState
from backend.schemas.zone import ComfortLimits, Zone
from backend.services.control import ControlService
from backend.services.data_quality import DataQualityGate
from backend.services.zone_state import ZoneStateService
from backend.safety.constraints import SafetyConstraintService


def make_state(*, count=12, temperature=24.0, setpoint=24.0, occupancy_time=None,
               temp_time=None, hvac_time=None, energy_power=1.2):
    now = utc_now()
    tariff_provider = MockTariffProvider()
    tariff = tariff_provider.get_current_tariff()
    occupancy = OccupancyEvent(zone_id="classroom_01", people_count=count, capacity=40,
        occupancy_percentage=count / 40 * 100, occupancy_state="MEDIUM" if count else "EMPTY",
        timestamp=occupancy_time or now, observed_at=occupancy_time or now,
        source="test_simulation", simulated=True)
    energy = EnergyReading(zone_id="classroom_01", power_kw=energy_power, energy_kwh=5.0,
        cost=0.75, tariff=tariff, is_simulated=True, observed_at=now, source="test_simulation")
    return ZoneState(
        zone=Zone(zone_id="classroom_01", name="Classroom", type="classroom", capacity=40,
            area_m2=60, comfort=ComfortLimits(min_temperature=22, max_temperature=26)),
        occupancy=occupancy, temperature=temperature, energy=energy, tariff=tariff,
        hvac_status=BACnetReadResult(zone_id="classroom_01", object_id="SIMULATED_POINT:setpoint",
            present_value=setpoint, provider="SIMULATED BACNET", timestamp=now,
            observed_at=hvac_time or now, setpoint_observed_at=hvac_time or now, simulated=True),
        occupancy_source="test_simulation", temperature_source="test_simulation",
        temperature_observed_at=temp_time or now, temperature_simulated=True,
    )


def advisory(state):
    return IntelligenceRecommendation(zone_id=state.zone.zone_id, recommended_setpoint=24.5,
        rationale="Test advisory", confidence=0.9, provider="mock_intelligence_provider",
        model_source="test", context_reference={"zone_id": state.zone.zone_id})


def test_quality_states_valid_stale_missing_invalid_and_out_of_range():
    now = utc_now()
    gate = DataQualityGate(max_age_seconds={"temperature": 10}, ranges={"temperature": (18, 30)})
    assert gate.assess("temperature", 22, source="test", observation_timestamp=now,
                       simulated=False, now=now, numeric=True).state == QualityState.VALID
    assert gate.assess("temperature", 22, source="test", observation_timestamp=now - timedelta(seconds=11),
                       simulated=False, now=now, numeric=True).state == QualityState.STALE
    assert gate.assess("temperature", None, source="test", required=True).state == QualityState.MISSING
    assert gate.assess("temperature", None, source="test", required=False).reason_code == "OPTIONAL_VALUE_MISSING"
    assert gate.assess("temperature", float("nan"), source="test", observation_timestamp=now,
                       simulated=True, now=now, numeric=True).state == QualityState.INVALID
    assert gate.assess("temperature", 17.9, source="test", observation_timestamp=now,
                       simulated=True, now=now, numeric=True).state == QualityState.OUT_OF_RANGE


def test_fresh_critical_inputs_are_valid_with_explicit_policy():
    now = utc_now()
    state = make_state(occupancy_time=now, temp_time=now, hvac_time=now)
    gate = DataQualityGate(max_age_seconds={
        "occupancy": 30, "temperature": 30, "setpoint": 30,
    })
    report = gate.assess_zone_state(state, now=now)
    assert {name: report.signals[name].state for name in
            ("occupancy", "temperature", "hvac_setpoint")} == {
                "occupancy": QualityState.VALID,
                "temperature": QualityState.VALID,
                "hvac_setpoint": QualityState.VALID,
            }


@pytest.mark.parametrize("signal,policy_signal,state_kwargs", [
    ("occupancy", "occupancy", {"occupancy_time": "stale"}),
    ("temperature", "temperature", {"temp_time": "stale"}),
    ("hvac_setpoint", "setpoint", {"hvac_time": "stale"}),
])
def test_each_stale_critical_input_is_classified_stale(signal, policy_signal, state_kwargs):
    old = utc_now() - timedelta(seconds=100)
    kwargs = {name: old if value == "stale" else value for name, value in state_kwargs.items()}
    report = DataQualityGate(max_age_seconds={policy_signal: 10}).assess_zone_state(make_state(**kwargs))
    assert report.signals[signal].state == QualityState.STALE


def test_future_critical_observation_is_invalid_and_missing_time_is_missing():
    future_state = make_state(temp_time=utc_now() + timedelta(seconds=2))
    missing_state = make_state()
    missing_state.temperature_observed_at = None
    missing_state.occupancy = missing_state.occupancy.model_copy(update={"observed_at": None})
    missing_state.hvac_status = missing_state.hvac_status.model_copy(update={"setpoint_observed_at": None,
                                                                              "observed_at": None})
    gate = DataQualityGate(max_age_seconds={"occupancy": 10, "temperature": 10, "setpoint": 10})
    future_report = gate.assess_zone_state(future_state)
    missing_report = gate.assess_zone_state(missing_state)
    assert future_report.signals["temperature"].state == QualityState.INVALID
    assert missing_report.signals["occupancy"].state == QualityState.MISSING
    assert missing_report.signals["temperature"].state == QualityState.MISSING
    assert missing_report.signals["hvac_setpoint"].state == QualityState.MISSING


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_invalid(bad):
    gate = DataQualityGate()
    assert gate.assess("x", bad, observation_timestamp=utc_now(), simulated=True,
                       numeric=True).state == QualityState.INVALID


@pytest.mark.parametrize("signal,value", [
    ("occupancy", -1), ("power_kw", -0.01), ("energy", -1.0), ("tariff_rate", -0.01),
])
def test_negative_count_energy_power_and_tariff_are_out_of_range(signal, value):
    gate = DataQualityGate()
    assert gate.assess(signal, value, observation_timestamp=utc_now(), simulated=True,
                       numeric=True, minimum=0).state == QualityState.OUT_OF_RANGE


@pytest.mark.parametrize("seconds,expected", [(1, QualityState.VALID), (6, QualityState.INVALID)])
def test_future_timestamp_and_clock_skew_policy(seconds, expected):
    now = utc_now()
    gate = DataQualityGate(future_clock_skew_seconds=5)
    observed_at = now + timedelta(seconds=seconds)
    state = gate.assess("x", 1, observation_timestamp=observed_at, simulated=True,
                        now=now, numeric=True).state
    assert state == expected


def test_timestamp_timezone_is_required_and_timezone_offsets_are_normalized():
    now = utc_now()
    gate = DataQualityGate(max_age_seconds={"x": 0})
    assert gate.assess("x", 1, observation_timestamp=now.replace(tzinfo=None), simulated=True,
                       now=now, numeric=True).state == QualityState.INVALID
    plus_zero = now.astimezone(now.tzinfo)
    assert gate.assess("x", 1, observation_timestamp=plus_zero, simulated=True,
                       now=now, numeric=True).state == QualityState.VALID
    # Legacy/generated event time alone is not accepted as an observation time.
    assert gate.assess("x", 1, observation_timestamp=None, simulated=False,
                       now=now, numeric=True).state == QualityState.MISSING


def test_configured_range_accepts_inclusive_boundaries():
    now = utc_now()
    gate = DataQualityGate(ranges={"temperature": (18, 30)})
    for value in (18, 30):
        assert gate.assess("temperature", value, observation_timestamp=now, simulated=True,
                           now=now, numeric=True).state == QualityState.VALID


def test_occupancy_count_capacity_percentage_and_state_consistency():
    gate = DataQualityGate()
    valid = make_state()
    assert gate.assess_zone_state(valid).signals["occupancy"].state == QualityState.VALID
    for updates in (
        {"people_count": -1}, {"people_count": 41}, {"occupancy_percentage": 70.0},
        {"occupancy_state": "EMPTY"},
    ):
        bad = make_state()
        bad.occupancy = bad.occupancy.model_copy(update=updates)
        assert gate.assess_zone_state(bad).signals["occupancy"].state != QualityState.VALID


def test_simulated_provenance_is_reported_without_claiming_real_sensor_data():
    report = DataQualityGate().assess_zone_state(make_state())
    assert report.signals["temperature"].simulated is True
    assert report.signals["temperature"].source == "test_simulation"
    assert report.signals["temperature"].state == QualityState.VALID


def test_zone_state_temperature_override_uses_simulated_control_provenance():
    control = MockBuildingControlProvider()
    zones = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
        MockEnergyProvider(MockTariffProvider()), control)
    state = zones.get_zone_state("classroom_01")
    assessment = state.data_quality.signals["temperature"]
    assert assessment.source == "SIMULATED BACNET"
    assert assessment.simulated is True
    assert assessment.observation_timestamp is not None
    assert assessment.state == QualityState.VALID


@pytest.mark.parametrize("policy_signal,state_kwargs", [
    ("occupancy", {"occupancy_time": "stale"}),
    ("temperature", {"temp_time": "stale"}),
    ("setpoint", {"hvac_time": "stale"}),
])
def test_recommendation_is_blocked_before_intelligence_for_stale_critical_quality(policy_signal, state_kwargs):
    class CalledProvider:
        provider_name = "called"
        def __init__(self): self.called = False
        def generate_recommendation(self, context: IntelligenceContext):
            self.called = True
            return advisory(make_state())
    provider = CalledProvider()
    old = utc_now() - timedelta(seconds=100)
    kwargs = {name: old if value == "stale" else value for name, value in state_kwargs.items()}
    state = make_state(**kwargs)
    workflow = RecommendationWorkflow(provider=provider,
        data_quality=DataQualityGate(max_age_seconds={policy_signal: 10}))
    decision = workflow.recommend(state)
    assert decision.recommendation_kind == "rejected"
    assert decision.validation.outcome == "REJECTED"
    assert provider.called is False


def test_fallback_is_quality_checked_independently():
    state = make_state()
    class Broken:
        def generate_recommendation(self, context):
            state.temperature_observed_at = utc_now() - timedelta(seconds=100)
            raise RuntimeError("offline")
    workflow = RecommendationWorkflow(provider=Broken(),
        data_quality=DataQualityGate(max_age_seconds={"temperature": 10}))
    decision = workflow.recommend(state)
    assert decision.validation.outcome == "REJECTED"
    assert decision.recommendation_kind == "rejected"
    assert state.data_quality.signals["temperature"].state == QualityState.STALE


def test_fallback_does_not_proceed_when_occupancy_becomes_stale():
    state = make_state()

    class BecomesStale:
        def generate_recommendation(self, context):
            state.occupancy = state.occupancy.model_copy(update={
                "observed_at": utc_now() - timedelta(seconds=100)})
            raise RuntimeError("offline")

    decision = RecommendationWorkflow(provider=BecomesStale(), data_quality=DataQualityGate(
        max_age_seconds={"occupancy": 10})).recommend(state)
    assert decision.validation.outcome == "REJECTED"
    assert decision.recommendation_kind == "rejected"
    assert decision.deterministic_recommendation is None


class CountingControlProvider(MockBuildingControlProvider):
    def __init__(self):
        super().__init__()
        self.write_count = 0

    def write_command(self, command):
        self.write_count += 1
        return super().write_command(command)


def test_control_rebuilds_current_state_and_rejects_bad_quality_without_write():
    state = make_state()
    provider = MockBuildingControlProvider()
    fresh_state = make_state(temp_time=utc_now() - timedelta(seconds=100))
    control = ControlService(provider, data_quality=DataQualityGate(max_age_seconds={"temperature": 10}),
        state_provider=lambda _zone: fresh_state)
    validation = SafetyConstraintService().validate(advisory(state), state)
    before = provider.read_status("classroom_01").present_value
    result = control.apply_validated_recommendation_result(validation, state)
    assert result.success is False
    assert result.error_code == "DATA_QUALITY_REJECTED"
    assert provider.read_status("classroom_01").present_value == before


def test_control_rejects_invalid_fresh_state_without_provider_write():
    state = make_state()
    provider = MockBuildingControlProvider()
    invalid = make_state(temperature=float("nan"))
    control = ControlService(provider, state_provider=lambda _zone: invalid)
    validation = SafetyConstraintService().validate(advisory(state), state)
    before = provider.read_status("classroom_01").present_value
    result = control.apply_validated_recommendation_result(validation, state)
    assert result.error_code == "DATA_QUALITY_REJECTED"
    assert provider.read_status("classroom_01").present_value == before


def test_fresh_recommendation_is_rejected_when_final_current_state_is_stale():
    state = make_state()
    stale = make_state(temp_time=utc_now() - timedelta(seconds=100))
    current_states = iter((state, stale))
    provider = CountingControlProvider()
    validation = SafetyConstraintService().validate(advisory(state), state)
    control = ControlService(provider, data_quality=DataQualityGate(
        max_age_seconds={"temperature": 10}), state_provider=lambda _zone: next(current_states))

    result = control.apply_validated_recommendation_result(validation, state)

    assert result.success is False
    assert result.error_code == "DATA_QUALITY_REJECTED"
    assert result.status == "REJECTED"
    assert provider.write_count == 0


def test_demo_monitoring_path_rejects_stale_occupancy_without_control_write():
    control_provider = CountingControlProvider()
    gate = DataQualityGate(max_age_seconds={"occupancy": 10})
    state_service = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
        MockEnergyProvider(MockTariffProvider()), control_provider, data_quality_gate=gate)
    workflow = RecommendationWorkflow(provider=MockIntelligenceProvider(), data_quality=gate)
    control = ControlService(control_provider, data_quality=gate,
        state_provider=state_service.get_zone_state)
    scheduler = ZoneMonitoringScheduler(state_service, workflow, control, MockOccupancyProvider())
    stale_occupancy = make_state(occupancy_time=utc_now() - timedelta(seconds=100)).occupancy

    result = asyncio.run(scheduler.process_simulated_occupancy(
        "classroom_01", stale_occupancy, "phase10_2_stale_test"))

    assert result["decision"].validation.outcome == "REJECTED"
    assert result["decision"].recommendation_kind == "rejected"
    assert control_provider.write_count == 0


def test_existing_safety_limits_remain_authoritative_after_quality_passes():
    state = make_state(setpoint=24)
    candidate = advisory(state).model_copy(update={"recommended_setpoint": 30.0})
    result = SafetyConstraintService().validate(candidate, state)
    assert result.outcome == "REJECTED"

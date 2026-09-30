from datetime import timedelta

import pytest

from backend.core.events import EventTrace
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider
from backend.intelligence.service import RecommendationWorkflow  # initialize shared safety dependency first
from backend.safety.constraints import SafetyConstraintService
from backend.services.control import ControlService
from backend.services.data_quality import DataQualityGate
from backend.core.time import utc_now

from tests.unit.test_simulated_bacnet_control import make_state, recommendation


def policy(**overrides):
    values = {"min_setpoint": 16, "max_setpoint": 30, "max_setpoint_delta": 2}
    values.update(overrides)
    return SafetyConstraintService(**values)


def command(state, setpoint=25, **overrides):
    values = {"zone_id": state.zone.zone_id, "setpoint": setpoint,
              "source": "test", "action_type": "setpoint_adjustment"}
    values.update(overrides)
    return values


def test_command_within_absolute_bounds_and_delta_is_valid():
    state = make_state(setpoint=24)
    assert policy().validate_command(command(state, 26), state).outcome == "VALIDATED"


@pytest.mark.parametrize("setpoint", [15.99, 30.01])
def test_command_outside_absolute_bounds_is_rejected(setpoint):
    state = make_state()
    assert policy().validate_command(command(state, setpoint), state).outcome == "REJECTED"


def test_exact_maximum_delta_is_accepted_and_larger_delta_is_rejected():
    state = make_state(setpoint=24)
    service = policy(max_setpoint_delta=1)
    assert service.validate_command(command(state, 25), state).outcome == "VALIDATED"
    assert service.validate_command(command(state, 25.01), state).outcome == "REJECTED"


@pytest.mark.parametrize("setpoint", [float("nan"), float("inf"), float("-inf"), "bad", None, True])
def test_malformed_or_non_finite_command_setpoint_is_rejected(setpoint):
    state = make_state()
    assert policy().validate_command(command(state, setpoint), state).outcome == "REJECTED"


def test_zone_mismatch_and_unsupported_action_are_rejected():
    state = make_state()
    service = policy()
    assert service.validate_command(command(state, zone_id="other"), state).outcome == "REJECTED"
    assert service.validate_command(command(state, action_type="power_off"), state).outcome == "REJECTED"
    assert service.validate_command({"zone_id": state.zone.zone_id}, state).outcome == "REJECTED"


def test_missing_or_invalid_policy_fails_closed(monkeypatch):
    for name in ("COMMAND_LIMIT_MIN_SETPOINT", "COMMAND_LIMIT_MAX_SETPOINT", "COMMAND_LIMIT_MAX_DELTA"):
        monkeypatch.delenv(name, raising=False)
    state = make_state()
    result = SafetyConstraintService().validate_command(command(state), state)
    assert result.outcome == "REJECTED"
    assert "policy" in result.rejection_reason.lower()


def test_fallback_recommendation_cannot_bypass_command_limits():
    state = make_state()
    fallback = recommendation(state, 26.1).model_copy(update={"provider": "deterministic_optimizer"})
    result = policy().validate_fallback(fallback, state, "provider unavailable")
    assert result.outcome == "REJECTED"


class CountingProvider(SimulatedBACnetBuildingControlProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.write_count = 0

    def write_command(self, command):
        self.write_count += 1
        return super().write_command(command)


def test_control_rejects_stale_state_without_provider_write():
    state = make_state(setpoint=24)
    service = policy()
    validated = service.validate(recommendation(state, 25), state)
    stale = state.model_copy(update={
        "occupancy": state.occupancy.model_copy(update={"observed_at": utc_now() - timedelta(seconds=10)}),
        "temperature_observed_at": utc_now() - timedelta(seconds=10),
        "hvac_status": state.hvac_status.model_copy(update={
            "observed_at": utc_now() - timedelta(seconds=10),
            "setpoint_observed_at": utc_now() - timedelta(seconds=10),
        }),
    })
    provider = CountingProvider(zone_ids=["test_zone"])
    gate = DataQualityGate(max_age_seconds={"occupancy": 1, "temperature": 1, "setpoint": 1})
    result = ControlService(provider, service, gate).apply_validated_recommendation_result(
        validated, state, current_state=stale,
    )
    assert result.status == "REJECTED"
    assert result.error_code == "DATA_QUALITY_REJECTED"
    assert provider.write_count == 0


def test_latest_current_setpoint_change_rejects_before_provider_write():
    state = make_state(setpoint=24)
    service = policy()
    validated = service.validate(recommendation(state, 25), state)
    changed = state.model_copy(update={
        "hvac_status": state.hvac_status.model_copy(update={"present_value": 21.5})
    })
    provider = CountingProvider(zone_ids=["test_zone"])
    result = ControlService(provider, service).apply_validated_recommendation_result(
        validated, state, current_state=changed,
    )
    assert result.status == "REJECTED"
    assert provider.write_count == 0


def test_control_validates_the_exact_command_before_provider_write():
    EventTrace.clear()
    state = make_state(setpoint=24)
    service = policy()
    validated_command = {}
    validate = service.validate_command

    def capture(command, current_state):
        validated_command.update(command.model_dump())
        return validate(command, current_state)

    service.validate_command = capture
    validated = service.validate(recommendation(state, 25), state)
    assert validated.outcome == "VALIDATED"
    provider = CountingProvider(zone_ids=["test_zone"])
    result = ControlService(provider, service).apply_validated_recommendation_result(validated, state)
    assert result.success is True
    assert provider.write_count == 1
    assert validated_command["zone_id"] == state.zone.zone_id
    assert validated_command["setpoint"] == 25
    assert validated_command["action_type"] == "setpoint_adjustment"


def test_final_command_limit_gate_can_reject_before_write(monkeypatch):
    state = make_state(setpoint=24)
    service = policy()
    validated = service.validate(recommendation(state, 25), state)
    provider = CountingProvider(zone_ids=["test_zone"])
    monkeypatch.setattr(service, "validate_command", lambda command, state: service._rejected(
        None, "Control command exceeds the configured per-command change limit."))
    result = ControlService(provider, service).apply_validated_recommendation_result(validated, state)
    assert result.status == "REJECTED"
    assert result.error_code == "COMMAND_LIMIT_REJECTED"
    assert provider.write_count == 0

import asyncio
from datetime import timedelta

import pytest

from backend.core.events import EventTrace
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider
from backend.intelligence.service import RecommendationWorkflow  # initialize shared safety dependency first
from backend.safety.constraints import SafetyConstraintService
from backend.security.roles import Role
from backend.services.control import ControlService
from backend.services.control_state import ZoneControlStateService
from backend.services.data_quality import DataQualityGate
from backend.core.time import utc_now
from tests.security_test_utils import make_client
from tests.unit.test_simulated_bacnet_control import make_state, recommendation


@pytest.fixture(autouse=True)
def reset_app_control_modes():
    from backend.api.main import control_service

    zones = ("classroom_01", "classroom_02", "lab_01", "lab_02")
    for zone_id in zones:
        control_service.control_states.set_manual_override(zone_id, False, user_id="test-cleanup")
        control_service.control_states.set_control_enabled(zone_id, True, user_id="test-cleanup")
    yield
    for zone_id in zones:
        control_service.control_states.set_manual_override(zone_id, False, user_id="test-cleanup")
        control_service.control_states.set_control_enabled(zone_id, True, user_id="test-cleanup")


class CountingProvider(SimulatedBACnetBuildingControlProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.write_count = 0

    def write_command(self, command):
        self.write_count += 1
        return super().write_command(command)


def make_safety():
    return SafetyConstraintService(min_setpoint=16, max_setpoint=30, max_setpoint_delta=2)


def validated(state, setpoint=25, safety=None):
    return (safety or make_safety()).validate(recommendation(state, setpoint), state)


def test_api_startup_control_state_is_fail_closed():
    from backend.api.main import control_service

    assert control_service.control_states.initially_enabled is False
    fresh_registry = ZoneControlStateService(initially_enabled=False)
    assert fresh_registry.snapshot("new-zone").control_enabled is False


def test_manual_override_changed_after_recommendation_blocks_without_write():
    EventTrace.clear()
    state = make_state(setpoint=24)
    safety = make_safety()
    decision = validated(state, 25, safety)
    provider = CountingProvider(zone_ids=[state.zone.zone_id])
    modes = ZoneControlStateService()
    modes.set_manual_override(state.zone.zone_id, True, user_id="operator")

    result = ControlService(provider, safety, control_states=modes).apply_validated_recommendation_result(decision, state)

    assert result.success is False
    assert result.error_code == "MANUAL_OVERRIDE_ACTIVE"
    assert provider.write_count == 0
    assert any(e.event_type == "COMMAND_BLOCKED_BY_OVERRIDE" for e in EventTrace.get_history(state.zone.zone_id))


def test_control_disabled_after_recommendation_blocks_without_write():
    EventTrace.clear()
    state = make_state(setpoint=24)
    safety = make_safety()
    decision = validated(state, 25, safety)
    provider = CountingProvider(zone_ids=[state.zone.zone_id])
    modes = ZoneControlStateService()
    modes.set_control_enabled(state.zone.zone_id, False, user_id="operator")

    result = ControlService(provider, safety, control_states=modes).apply_validated_recommendation_result(decision, state)

    assert result.success is False
    assert result.error_code == "CONTROL_DISABLED"
    assert provider.write_count == 0
    assert any(e.event_type == "COMMAND_BLOCKED_BY_CONTROL_DISABLE" for e in EventTrace.get_history(state.zone.zone_id))


def test_autonomous_demo_path_obeys_manual_override():
    from backend.core.monitoring import ZoneMonitoringScheduler
    from backend.core.mock_providers import MockEnergyProvider, MockOccupancyProvider, MockTariffProvider, MockTemperatureProvider
    from backend.demo.scenario import DemoScenarioOccupancyProvider
    from backend.intelligence.service import RecommendationWorkflow
    from backend.services.zone_state import ZoneStateService

    zone_id = "classroom_01"
    provider = CountingProvider()
    state_service = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
                                     MockEnergyProvider(MockTariffProvider()), provider)
    modes = ZoneControlStateService()
    service = ControlService(provider, make_safety(), state_provider=state_service.get_zone_state,
                             control_states=modes)
    scheduler = ZoneMonitoringScheduler(state_service, RecommendationWorkflow(), service, MockOccupancyProvider())
    occupancy_source = DemoScenarioOccupancyProvider({
        zone_id: state_service._zones[zone_id].capacity,
    })
    occupancy_source.set_counts((zone_id,), (35,))
    modes.set_manual_override(zone_id, True, user_id="operator")
    outcome = asyncio.run(scheduler.process_simulated_occupancy(
        zone_id, occupancy_source.get_occupancy(zone_id), "scenario-manual-override-test",
    ))
    assert outcome["decision"].validation.outcome in {"VALIDATED", "FALLBACK"}
    assert provider.write_count == 0
    assert service.last_results[zone_id].error_code == "MANUAL_OVERRIDE_ACTIVE"


def test_provider_failure_fails_safely_and_recovery_does_not_resume_automatically():
    EventTrace.clear()
    state = make_state(setpoint=24)
    safety = make_safety()
    decision = validated(state, 25, safety)
    provider = CountingProvider(zone_ids=[state.zone.zone_id], failure_mode="acknowledgement")
    modes = ZoneControlStateService()
    service = ControlService(provider, safety, control_states=modes)

    failed = service.apply_validated_recommendation_result(decision, state)
    assert failed.success is False and failed.status == "FAILED"
    assert modes.snapshot(state.zone.zone_id).control_enabled is False
    assert modes.snapshot(state.zone.zone_id).fail_safe_active is True
    assert [e.event_type for e in EventTrace.get_history(state.zone.zone_id)].count("PROVIDER_FAILURE") == 1
    assert any(e.event_type == "FAIL_SAFE_ACTIVATED" for e in EventTrace.get_history(state.zone.zone_id))
    assert any(e.event_type == "CONTROL_DISABLED" for e in EventTrace.get_history(state.zone.zone_id))

    provider.failure_mode = "unavailable"
    blocked = service.apply_validated_recommendation_result(decision, state)
    assert blocked.success is False and blocked.error_code == "CONTROL_DISABLED"
    assert provider.write_count == 1
    assert modes.snapshot(state.zone.zone_id).control_enabled is False
    provider.failure_mode = None
    blocked_after_recovery = service.apply_validated_recommendation_result(decision, state)
    assert blocked_after_recovery.success is False and blocked_after_recovery.error_code == "CONTROL_DISABLED"
    assert provider.write_count == 1
    assert any(e.event_type == "PROVIDER_RECOVERY" for e in EventTrace.get_history(state.zone.zone_id))

    modes.set_control_enabled(state.zone.zone_id, True, user_id="operator")
    resumed = service.apply_validated_recommendation_result(validated(state, 25, safety), state)
    assert resumed.success is True
    assert provider.write_count == 2
    assert any(e.event_type == "AUTO_CONTROL_RESUMED" for e in EventTrace.get_history(state.zone.zone_id))


def test_reenable_does_not_bypass_freshness_or_safety_validation():
    state = make_state(setpoint=24)
    safety = make_safety()
    modes = ZoneControlStateService()
    modes.set_control_enabled(state.zone.zone_id, False, user_id="operator")
    modes.set_control_enabled(state.zone.zone_id, True, user_id="operator")
    provider = CountingProvider(zone_ids=[state.zone.zone_id])
    stale = state.model_copy(update={
        "occupancy": state.occupancy.model_copy(update={"observed_at": utc_now() - timedelta(seconds=10)}),
        "temperature_observed_at": utc_now() - timedelta(seconds=10),
        "hvac_status": state.hvac_status.model_copy(update={
            "observed_at": utc_now() - timedelta(seconds=10),
            "setpoint_observed_at": utc_now() - timedelta(seconds=10),
        }),
    })
    quality = DataQualityGate(max_age_seconds={"occupancy": 1, "temperature": 1, "setpoint": 1})
    service = ControlService(provider, safety, quality, control_states=modes)
    result = service.apply_validated_recommendation_result(validated(state, 25, safety), stale)
    assert result.error_code == "DATA_QUALITY_REJECTED"
    assert provider.write_count == 0

    unsafe = safety.validate(recommendation(state, 29), state)
    assert unsafe.outcome == "REJECTED"
    rejected = service.apply_validated_recommendation_result(unsafe, state)
    assert rejected.success is False
    assert provider.write_count == 0


def test_operator_can_manage_control_state_and_audit_is_recorded(monkeypatch):
    from backend.api.main import app, control_service, zone_state_service
    from backend.core.mock_providers import MockOccupancyProvider

    zone_id = "classroom_01"
    modes = control_service.control_states
    modes.set_manual_override(zone_id, False, user_id="test-cleanup")
    modes.set_control_enabled(zone_id, True, user_id="test-cleanup")
    monkeypatch.setattr(zone_state_service, "occupancy_provider", MockOccupancyProvider())
    EventTrace.clear()
    client = make_client()

    enabled = client.post(f"/api/zones/{zone_id}/manual-override", json={"enabled": True})
    assert enabled.status_code == 200 and enabled.json()["manual_override"] is True
    visible = client.get(f"/api/zones/{zone_id}/state")
    assert visible.status_code == 200
    assert visible.json()["control_mode"]["manual_override"] is True
    disabled = client.post(f"/api/zones/{zone_id}/control-enabled", json={"enabled": False})
    assert disabled.status_code == 200 and disabled.json()["control_enabled"] is False
    manual_off = client.post(f"/api/zones/{zone_id}/manual-override", json={"enabled": False})
    control_on = client.post(f"/api/zones/{zone_id}/control-enabled", json={"enabled": True})
    assert manual_off.status_code == 200
    assert control_on.status_code == 200, control_on.text
    assert control_on.json()["control_enabled"] is True
    actions = {row.action for row in app.state.audit_service.list_records()}
    assert {"manual_override_enabled", "manual_override_disabled", "control_disabled", "control_enabled"} <= actions
    events = {event.event_type for event in EventTrace.get_history(zone_id)}
    assert {"MANUAL_OVERRIDE_ENABLED", "MANUAL_OVERRIDE_DISABLED", "CONTROL_DISABLED", "CONTROL_ENABLED"} <= events


def test_admin_cannot_change_control_modes_and_unassigned_operator_is_blocked():
    admin = make_client(role=Role.ADMIN)
    zone_id = "classroom_01"
    assert admin.post(f"/api/zones/{zone_id}/manual-override", json={"enabled": True}).status_code == 403
    assert admin.post(f"/api/zones/{zone_id}/control-enabled", json={"enabled": False}).status_code == 403
    unassigned = make_client(building_ids=set())
    assert unassigned.post(f"/api/zones/{zone_id}/manual-override", json={"enabled": True}).status_code == 403


def test_control_reenable_requires_fresh_data_and_audits_rejection(monkeypatch):
    from backend.api.main import app, control_service

    zone_id = "classroom_01"
    modes = control_service.control_states
    modes.set_manual_override(zone_id, False, user_id="test-cleanup")
    modes.set_control_enabled(zone_id, False, user_id="test-setup")
    monkeypatch.setattr(control_service.data_quality, "critical_failures", lambda report: {"temperature": object()})
    response = make_client().post(f"/api/zones/{zone_id}/control-enabled", json={"enabled": True})
    assert response.status_code == 409
    assert modes.snapshot(zone_id).control_enabled is False
    assert any(row.action == "control_enable_rejected" and row.success is False
               for row in app.state.audit_service.list_records())

from uuid import UUID, uuid4

from backend.api.main import app
from backend.database.models import IntegrationRecord
from backend.integrations.discovery import SimulatedFixtureDiscoveryProvider
from backend.integrations.lifecycle import (BoundedRetryPolicy, ConnectionState,
    IntegrationLifecycleManager)
from backend.integrations.mapping_suggestions import suggest_mapping
from backend.security.roles import Role
from tests.security_test_utils import make_client


def _building_id(client):
    return next(row["building_id"] for row in client.get("/api/buildings").json()["buildings"]
                if row["building_key"] == "development-building")


def _integration(client, *, kind="BACNET", config=None):
    return client.post(f"/api/buildings/{_building_id(client)}/integrations", json={
        "name": f"phase13-{uuid4().hex[:10]}", "integration_type": kind,
        "configuration": config or {"host": "demo.invalid"}}).json()


def test_lifecycle_states_and_bounded_retry_policy_are_explicit():
    assert {state.value for state in ConnectionState} == {
        "DISCONNECTED", "CONNECTING", "CONNECTED", "DEGRADED", "ERROR"}
    policy = BoundedRetryPolicy(max_attempts=3, initial_delay_seconds=2, maximum_delay_seconds=5)
    assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [2, 4, None, None]


def test_connection_failure_is_recorded_and_recovery_requires_explicit_transitions():
    client = make_client(Role.OPERATOR, {"development-building"})
    integration = _integration(client)
    identifier = integration["integration_id"]
    with app.state.database_sessions.begin() as session:
        row = session.get(IntegrationRecord, UUID(identifier))
        IntegrationLifecycleManager.transition(session, row, ConnectionState.CONNECTING,
            source="future_adapter", simulated=False)
        IntegrationLifecycleManager.transition(session, row, ConnectionState.ERROR,
            source="future_adapter", simulated=False, error_code="CONNECT_TIMEOUT")
        assert row.last_error == "CONNECT_TIMEOUT"
        assert row.commissioning_state == "BLOCKED"
        IntegrationLifecycleManager.transition(session, row, ConnectionState.CONNECTING,
            source="future_adapter", simulated=False)
        IntegrationLifecycleManager.transition(session, row, ConnectionState.CONNECTED,
            source="future_adapter", simulated=False)
        assert row.commissioning_state == "BLOCKED"
        assert row.connection_state == "CONNECTED"
        try:
            IntegrationLifecycleManager.transition(session, row, ConnectionState.ERROR,
                source="future_adapter", simulated=False, error_code="BearerSECRET")
        except ValueError:
            pass
        else:
            raise AssertionError("Unstructured error text must never be accepted into lifecycle storage")
    trace = client.get(f"/api/integrations/{identifier}/lifecycle").json()
    assert [item["new_state"] for item in trace["transitions"] if item["event_type"].startswith("CONNECTION_")][-4:] == [
        "CONNECTING", "ERROR", "CONNECTING", "CONNECTED"]


def test_fixture_discovery_is_deterministic_and_mapping_suggestion_never_confirms():
    provider = SimulatedFixtureDiscoveryProvider()
    first = provider.discover("BACNET")
    second = provider.discover("BACNET")
    assert first.simulated == second.simulated and first.performed == second.performed
    assert first.candidates[0]["external_device_id"] == second.candidates[0]["external_device_id"]
    assert first.simulated and first.performed
    assert first.candidates[0]["source"] == "SIMULATED_FIXTURE"
    assert first.candidates[0]["discovery_timestamp"]
    assert first.candidates[0]["points"][0]["discovery_timestamp"]
    assert first.candidates[0]["points"][0]["quality_status"] == "SIMULATED_FIXTURE"
    assert suggest_mapping(signal="temperature", unit="°C", data_type="number",
        readable=True, zone_owned=True) == ("SUGGESTED", 1.0, "EXACT_SIGNAL_AND_UNIT_MATCH")
    assert suggest_mapping(signal="temperature", unit="kW", data_type="number",
        readable=True, zone_owned=True)[0] == "UNMAPPED"
    assert suggest_mapping(signal="temperature", unit="°C", data_type="number",
        readable=True, zone_owned=False)[0] == "UNMAPPED"


def test_configuration_validation_and_fixture_discovery_never_claim_physical_readiness():
    client = make_client(Role.OPERATOR, {"development-building"})
    integration = _integration(client, config={"host": "demo.invalid",
        "verified_source_capability": True})
    identifier = integration["integration_id"]
    result = client.post(f"/api/integrations/{identifier}/test-connection").json()
    assert result["result"] == "CONFIGURATION_VALID"
    assert result["connection_established"] is False
    assert result["connection_state"] == "DISCONNECTED"
    assert result["physical_connection_attempted"] is False
    discovery = client.post(f"/api/integrations/{identifier}/discover").json()
    assert discovery["simulated"] and discovery["discovery_performed"]
    assert "SIMULATED FIXTURES" in discovery["message"]
    assert len(discovery["devices"]) == 1 and len(discovery["points"]) == 2
    repeated = client.post(f"/api/integrations/{identifier}/discover").json()
    assert repeated["devices"] == discovery["devices"]
    assert repeated["points"] == discovery["points"]
    commissioning = client.get(f"/api/integrations/{identifier}/commissioning").json()
    health = client.get(f"/api/integrations/{identifier}/health").json()
    assert commissioning["read_only_ready"] is False
    assert commissioning["physical_source_verified"] is False
    assert commissioning["state"] == "SIMULATED_COMMISSIONING"
    assert health["physical_connection_implemented"] is False
    trace = client.get(f"/api/integrations/{identifier}/lifecycle").json()
    assert any(event["event_type"] == "SIMULATED_DISCOVERY" for event in trace["transitions"])
    assert all(event["simulated"] for event in trace["transitions"])


def test_integration_commissioning_and_health_remain_building_scoped():
    operator = make_client(Role.OPERATOR, {"development-building"})
    integration = _integration(operator)
    identifier = integration["integration_id"]
    other = make_client(Role.OPERATOR, set())
    assert other.get(f"/api/integrations/{identifier}/commissioning").status_code == 403
    assert other.get(f"/api/integrations/{identifier}/health").status_code == 403


def test_admin_cannot_run_configuration_or_discovery_actions():
    operator = make_client(Role.OPERATOR, {"development-building"})
    integration = _integration(operator)
    admin = make_client(Role.ADMIN)
    identifier = integration["integration_id"]
    assert admin.post(f"/api/integrations/{identifier}/test-connection").status_code == 403
    assert admin.post(f"/api/integrations/{identifier}/discover").status_code == 403

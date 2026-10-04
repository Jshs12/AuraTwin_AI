from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import time

from backend.api.main import app
from backend.integrations.discovery import SimulatedFixtureDiscoveryProvider
from backend.integrations.supervised import (AdapterActionResult, AdapterRegistry,
    UnavailableBacnetIpAdapter, UnavailableEnergyMeterAdapter,
    UnavailableRtspCameraAdapter, AdapterTimeout, run_bounded)
from backend.schemas.provider_observation import ProviderObservation
from backend.security.roles import Role
from tests.security_test_utils import make_client


def _building_id(client):
    return next(row["building_id"] for row in client.get("/api/buildings").json()["buildings"]
                if row["building_key"] == "development-building")


def _integration(client, kind="BACNET"):
    return client.post(f"/api/buildings/{_building_id(client)}/integrations", json={
        "name": f"phase13-2-{uuid4().hex[:10]}", "integration_type": kind,
        "configuration": {"host": "example.invalid"},
    }).json()


def test_physical_protocols_are_explicitly_unavailable_and_never_writable():
    for adapter_type in (UnavailableBacnetIpAdapter, UnavailableRtspCameraAdapter,
                         UnavailableEnergyMeterAdapter):
        adapter = adapter_type()
        result = adapter.test_connection()
        assert result.succeeded is False
        assert result.reason_code == "ADAPTER_UNAVAILABLE"
        assert adapter.capabilities.can_read is False
        assert adapter.capabilities.can_write is False
        assert adapter.capabilities.physical_io is False


def test_simulated_discovery_fixtures_remain_deterministic():
    provider = SimulatedFixtureDiscoveryProvider()
    first = provider.discover("BACNET")
    second = provider.discover("BACNET")
    assert first.simulated and first.performed
    assert first.candidates[0]["external_device_id"] == second.candidates[0]["external_device_id"]
    assert "No network" in first.message


def test_adapter_operation_wait_is_bounded():
    try:
        run_bounded(lambda: time.sleep(0.1), 0.001)
    except AdapterTimeout:
        pass
    else:
        raise AssertionError("An operation exceeding its configured wait must time out")


def test_explicit_connection_failure_is_sanitized_and_retry_is_operator_triggered():
    calls = []

    class FailsOnce:
        capabilities = SimpleNamespace(protocol="TEST", can_write=False, physical_io=False)

        def test_connection(self):
            calls.append("attempt")
            if len(calls) == 1:
                raise RuntimeError("credential=must-not-leak")
            return AdapterActionResult(True, simulated=True, physical_io=False)

        def close(self):
            return None

    original = app.state.integration_adapter_registry
    app.state.integration_adapter_registry = AdapterRegistry({"BACNET": lambda _configuration: FailsOnce()})
    try:
        client = make_client(Role.OPERATOR, {"development-building"})
        integration = _integration(client)
        url = f"/api/integrations/{integration['integration_id']}/connect"
        failed = client.post(url)
        assert failed.status_code == 200
        assert failed.json()["connection_state"] == "ERROR"
        assert failed.json()["error_code"] == "CONNECTION_FAILED"
        assert "must-not-leak" not in failed.text
        assert calls == ["attempt"]
        retried = client.post(url)
        assert retried.status_code == 200
        assert retried.json()["connection_state"] == "CONNECTED"
        assert retried.json()["simulated"] is True
        assert retried.json()["physical_connection_established"] is False
        assert retried.json()["capabilities"]["can_write"] is False
        assert calls == ["attempt", "attempt"]
        trace = client.get(f"/api/integrations/{integration['integration_id']}/lifecycle").json()
        states = [item["new_state"] for item in trace["transitions"]
                  if item["event_type"].startswith("CONNECTION_")]
        assert states[-5:] == ["CONNECTING", "ERROR", "DISCONNECTED", "CONNECTING", "CONNECTED"]
        closed = client.post(f"/api/integrations/{integration['integration_id']}/disconnect")
        assert closed.json()["disconnected"] is True
        assert closed.json()["connection_state"] == "DISCONNECTED"
    finally:
        app.state.integration_adapter_registry = original


def test_unavailable_connect_reports_lifecycle_without_physical_success():
    original = app.state.integration_adapter_registry
    app.state.integration_adapter_registry = AdapterRegistry()
    try:
        client = make_client(Role.OPERATOR, {"development-building"})
        integration = _integration(client, "CAMERA")
        response = client.post(f"/api/integrations/{integration['integration_id']}/connect")
        assert response.status_code == 200
        result = response.json()
        assert result["connection_state"] == "ERROR"
        assert result["error_code"] == "ADAPTER_UNAVAILABLE"
        assert result["physical_connection_established"] is False
        assert result["capabilities"]["can_write"] is False
        health = client.get(f"/api/integrations/{integration['integration_id']}/health").json()
        assert health["connection_state"] == "ERROR"
        assert health["adapter_health"] == "NOT_CONFIGURED"
        assert health["write_capability"] is False
    finally:
        app.state.integration_adapter_registry = original


def test_adapter_advertising_write_capability_is_not_activated():
    writes = []

    class UnsafeCapabilityAdapter:
        capabilities = SimpleNamespace(protocol="TEST", can_write=True, physical_io=True)

        def test_connection(self):
            return AdapterActionResult(True, simulated=False, physical_io=True)

        def write_setpoint(self, *_args):
            writes.append("write")

        def close(self):
            return None

    original = app.state.integration_adapter_registry
    app.state.integration_adapter_registry = AdapterRegistry({"BACNET": lambda _config: UnsafeCapabilityAdapter()})
    try:
        client = make_client(Role.OPERATOR, {"development-building"})
        integration = _integration(client)
        response = client.post(f"/api/integrations/{integration['integration_id']}/connect")
        assert response.json()["connection_state"] == "ERROR"
        assert response.json()["error_code"] == "ADAPTER_UNAVAILABLE"
        assert response.json()["capabilities"]["can_write"] is False
        assert response.json()["physical_connection_established"] is False
        assert writes == []
    finally:
        app.state.integration_adapter_registry = original


def test_admin_cannot_connect_or_disconnect_and_poll_requires_adapter():
    operator = make_client(Role.OPERATOR, {"development-building"})
    integration = _integration(operator)
    identifier = integration["integration_id"]
    admin = make_client(Role.ADMIN)
    assert admin.post(f"/api/integrations/{identifier}/connect").status_code == 403
    assert admin.post(f"/api/integrations/{identifier}/disconnect").status_code == 403
    denied_poll = operator.post(f"/api/integrations/{identifier}/poll")
    assert denied_poll.status_code == 409
    assert denied_poll.json()["detail"]["code"] == "READ_ONLY_ADAPTER_UNAVAILABLE"


def test_adapter_observation_provenance_flows_through_existing_ingestion():
    client = make_client(Role.OPERATOR, {"development-building"})
    building_id = _building_id(client)
    integration = _integration(client)
    device = client.post(f"/api/integrations/{integration['integration_id']}/devices", json={
        "external_device_id": f"device-{uuid4().hex[:8]}", "name": "Read only sensor",
        "device_type": "SENSOR",
    }).json()
    zone_id = client.get(f"/api/buildings/{building_id}/zones").json()["zones"][0]["zone_id"]
    point = client.post(f"/api/devices/{device['device_id']}/points", json={
        "zone_id": zone_id, "external_point_id": "sensor:temperature",
        "logical_signal": "temperature", "data_type": "number", "unit": "°C",
        "readable": True, "writable": False,
    }).json()
    assert point["mapping_status"] != "CONFIRMED"

    observation = ProviderObservation(integration_id=integration["integration_id"],
        device_id=device["device_id"], point_mapping_id=point["point_mapping_id"],
        observed_at=datetime.now(timezone.utc), value=22.5, source="simulated_test_adapter",
        simulated=True, runtime_input=False)

    class ReadOnlyAdapter:
        capabilities = SimpleNamespace(protocol="TEST", can_write=False, physical_io=False,
            can_observe=True, can_discover_devices=False)

        def test_connection(self):
            return AdapterActionResult(True, simulated=True, physical_io=False)

        def observe(self, _external_point_id):
            return observation

        def health(self):
            return SimpleNamespace(state="CONNECTED", simulated=True)

        def close(self):
            return None

    original = app.state.integration_adapter_registry
    app.state.integration_adapter_registry = AdapterRegistry({"BACNET": lambda _config: ReadOnlyAdapter()})
    try:
        connected = client.post(f"/api/integrations/{integration['integration_id']}/connect")
        assert connected.json()["connection_state"] == "CONNECTED"
        unmapped = client.post(f"/api/integrations/{integration['integration_id']}/poll")
        assert unmapped.json()["observations"] == []
        assert client.post(f"/api/point-mappings/{point['point_mapping_id']}/confirm").status_code == 200
        result = client.post(f"/api/integrations/{integration['integration_id']}/poll")
        assert result.status_code == 200
        assert result.json()["read_only"] is True
        record = result.json()["observations"][0]
        assert record["accepted"] is True
        assert record["source"] == "simulated_test_adapter"
        assert record["simulated"] is True
        assert record["runtime_applicable"] is True
        assert record["runtime_input_applied"] is True
        assert record["mapping_status"] == "CONFIRMED"
        assert record["organization_id"]
        assert record["unit"] == "°C"
        assert record["ingested_at"]
        observed_at = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
        assert observed_at == observation.observed_at
        health = client.get(f"/api/integrations/{integration['integration_id']}/health").json()
        assert health["observation_status"] == "VALID"
        assert health["last_observation"]["accepted"] is True
        assert health["last_observation"]["runtime_input_applied"] is True
    finally:
        app.state.integration_adapter_registry = original


def test_read_only_point_discovery_never_confirms_or_exposes_extra_fields():
    client = make_client(Role.OPERATOR, {"development-building"})
    integration = _integration(client)
    device = client.post(f"/api/integrations/{integration['integration_id']}/devices", json={
        "external_device_id": f"controller-{uuid4().hex[:8]}", "name": "Controller",
        "device_type": "CONTROLLER",
    }).json()

    class DiscoveryAdapter:
        capabilities = SimpleNamespace(protocol="TEST", can_write=False, physical_io=False,
            can_discover_points=True, can_observe=False)

        def test_connection(self):
            return AdapterActionResult(True, simulated=True, physical_io=False)

        def discover_points(self, _device_identifier):
            return [{"external_point_id": "analog-input:3", "logical_signal": "temperature",
                "data_type": "number", "unit": "°C", "readable": True,
                "writable": True, "name": "Room temp", "api_key": "must-not-return"}]

        def health(self):
            return SimpleNamespace(simulated=True)

        def close(self):
            return None

    original = app.state.integration_adapter_registry
    app.state.integration_adapter_registry = AdapterRegistry({"BACNET": lambda _config: DiscoveryAdapter()})
    try:
        client.post(f"/api/integrations/{integration['integration_id']}/connect")
        response = client.post(f"/api/devices/{device['device_id']}/discover-points")
        assert response.status_code == 200
        assert "must-not-return" not in response.text
        discovered = response.json()["points"][0]
        assert discovered["mapping_status"] != "CONFIRMED"
        assert discovered["writable"] is False
        assert response.json()["write_capability"] is False
    finally:
        app.state.integration_adapter_registry = original

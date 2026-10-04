from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from backend.api.main import app
from backend.edge.service import EdgeConnectorService
from backend.integrations.supervised import AdapterRegistry, AdapterActionResult
from backend.schemas.edge import EdgeConfiguration, EdgeMode
from backend.schemas.provider_observation import ProviderObservation
from backend.security.roles import Role
from tests.security_test_utils import make_client


def _building(client):
    return next(row for row in client.get("/api/buildings").json()["buildings"]
                if row["building_key"] == "development-building")


def test_edge_orchestrates_connected_read_only_adapter_through_existing_ingestion():
    client = make_client(Role.OPERATOR, {"development-building"})
    building = _building(client)
    integration = client.post(f"/api/buildings/{building['building_id']}/integrations", json={
        "name": f"edge-test-{uuid4().hex[:8]}", "integration_type": "BACNET",
        "configuration": {"host": "simulated-only"},
    }).json()
    device = client.post(f"/api/integrations/{integration['integration_id']}/devices", json={
        "external_device_id": f"edge-device-{uuid4().hex[:8]}",
        "name": "Simulated read-only temperature point", "device_type": "SENSOR",
    }).json()
    zone = client.get(f"/api/buildings/{building['building_id']}/zones").json()["zones"][0]
    point = client.post(f"/api/devices/{device['device_id']}/points", json={
        "zone_id": zone["zone_id"], "external_point_id": "space-temperature",
        "logical_signal": "temperature", "data_type": "number", "unit": "°C",
        "readable": True, "writable": False,
    }).json()
    assert client.post(f"/api/point-mappings/{point['point_mapping_id']}/confirm").status_code == 200
    observation = ProviderObservation(integration_id=integration["integration_id"],
        device_id=device["device_id"], point_mapping_id=point["point_mapping_id"],
        observed_at=datetime.now(timezone.utc), value=22.0, source="edge_test_adapter",
        simulated=True, signal="temperature", unit="°C")

    class ReadOnlyAdapter:
        capabilities = SimpleNamespace(protocol="BACNET/IP", can_observe=True,
            can_write=False, physical_io=False)

        def test_connection(self):
            return AdapterActionResult(True, simulated=True, physical_io=False)

        def health(self):
            return SimpleNamespace(state="CONNECTED", simulated=True)

        def observe(self, _point):
            return observation

        def close(self):
            return None

        def write(self, *_args, **_kwargs):
            raise AssertionError("Edge foundation must never call an adapter write")

    original_registry = app.state.integration_adapter_registry
    original_edge = app.state.edge_connector
    registry = AdapterRegistry({"BACNET": lambda _configuration: ReadOnlyAdapter()})
    app.state.integration_adapter_registry = registry
    try:
        assert client.post(f"/api/integrations/{integration['integration_id']}/connect").json()["connection_state"] == "CONNECTED"
        edge = EdgeConnectorService(configuration=app.state.configuration_repository,
            sessions=app.state.database_sessions, adapter_registry=registry,
            ingestion_service=app.state.provider_observation_ingestion_service,
            config=EdgeConfiguration(edge_id="scoped-edge", building_id=building["building_id"],
                expected_organization_id=building["organization_id"], mode=EdgeMode.SIMULATED))
        app.state.edge_connector = edge
        assert edge.start().building_id == building["building_id"]
        result = edge.poll_integration(integration["integration_id"])
        assert result["accepted"] is True
        assert result["read_only"] is True and result["write_capability"] is False
        assert result["observations"][0]["messages"][0]["ingestion_accepted"] is True
        envelope = edge.transport.messages[0]
        assert envelope.organization_id == building["organization_id"]
        assert envelope.building_id == building["building_id"]
        assert envelope.zone_id == zone["database_zone_id"]
        assert envelope.simulated is True
        assert "READ_BACNET" in {item.value for item in edge.status().capabilities}
        edge.stop()
        client.post(f"/api/integrations/{integration['integration_id']}/disconnect")
    finally:
        app.state.edge_connector = original_edge
        app.state.integration_adapter_registry = original_registry

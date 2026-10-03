from datetime import datetime, timezone
from uuid import uuid4

from backend.database.repositories import OrganizationData
from backend.api.main import app
from backend.security.roles import Role
from tests.security_test_utils import bare_client, make_client


def _building_id(client):
    response = client.get("/api/buildings")
    assert response.status_code == 200
    return next(item["building_id"] for item in response.json()["buildings"]
                if item["building_key"] == "development-building")


def test_integration_device_point_mapping_lifecycle_and_simulated_adapters():
    client = make_client(Role.OPERATOR, {"development-building"})
    building_id = _building_id(client)
    created = client.post(f"/api/buildings/{building_id}/integrations", json={
        "name": f"test-{uuid4().hex[:8]}", "integration_type": "BACNET",
        "configuration": {"host": "127.0.0.1", "port": 47808},
        "credential_reference": "vault://building/controller",
    })
    assert created.status_code == 201
    integration = created.json()
    assert integration["status"] == "CONFIGURED"
    assert integration["credential_configured"] is True
    assert "credential_reference" not in integration
    listed = client.get(f"/api/buildings/{building_id}/integrations").json()["integrations"]
    assert integration["integration_id"] in {item["integration_id"] for item in listed}

    test_result = client.post(f"/api/integrations/{integration['integration_id']}/test-connection").json()
    assert test_result["simulated"] is True
    assert test_result["connection_established"] is False
    assert test_result["result"] == "CONFIGURATION_VALID"
    assert test_result["commissioning_state"] == "CONFIGURED"
    assert test_result["connection_state"] == "DISCONNECTED"
    assert test_result["physical_connection_attempted"] is False
    discovery = client.post(f"/api/integrations/{integration['integration_id']}/discover").json()
    assert discovery["simulated"] is True and discovery["discovery_performed"] is True
    assert len(discovery["candidates"]) == 1
    assert "SIMULATED FIXTURES" in discovery["message"]
    repeated = client.post(f"/api/integrations/{integration['integration_id']}/discover").json()
    assert repeated["devices"] == discovery["devices"]
    assert repeated["points"] == discovery["points"]
    commissioning = client.get(f"/api/integrations/{integration['integration_id']}/commissioning").json()
    assert commissioning["state"] == "SIMULATED_COMMISSIONING"
    assert commissioning["read_only_ready"] is False

    device = client.post(f"/api/integrations/{integration['integration_id']}/devices", json={
        "external_device_id": f"device-{uuid4().hex[:8]}", "name": "Controller",
        "device_type": "HVAC_CONTROLLER", "manufacturer": "Unknown", "model": None,
    })
    assert device.status_code == 201
    zone_id = client.get(f"/api/buildings/{building_id}/zones").json()["zones"][0]["zone_id"]
    point = client.post(f"/api/devices/{device.json()['device_id']}/points", json={
        "zone_id": zone_id,
        "external_point_id": "AI:1:presentValue", "logical_signal": "temperature",
        "data_type": "number", "unit": "C", "readable": True, "writable": False,
        "mapping_status": "SUGGESTED", "mapping_confidence": 0.82,
    })
    assert point.status_code == 201
    point_id = point.json()["point_mapping_id"]
    assert point.json()["mapping_status"] == "SUGGESTED"
    confirmed = client.post(f"/api/point-mappings/{point_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["mapping_status"] == "CONFIRMED"
    observed = client.post(f"/api/point-mappings/{point_id}/simulated-observation", json={
        "value": 21.0, "observed_at": datetime.now(timezone.utc).isoformat()})
    assert observed.status_code == 200
    health = client.get(f"/api/integrations/{integration['integration_id']}/health").json()
    temperature_health = next(signal for signal in health["signals"]
                              if signal["point_mapping_id"] == point_id)
    assert temperature_health["quality_state"] == "VALID"
    assert temperature_health["simulated"] is True
    assert client.post(f"/api/point-mappings/{point_id}/reject").json()["mapping_status"] == "REJECTED"
    assert client.delete(f"/api/point-mappings/{point_id}").json()["mapping_status"] == "INACTIVE"
    assert client.get(f"/api/devices/{device.json()['device_id']}/points").json()["points"][0]["point_mapping_id"] == point_id

    assert client.patch(f"/api/integrations/{integration['integration_id']}", json={"name": "renamed"}).json()["name"] == "renamed"
    assert client.delete(f"/api/integrations/{integration['integration_id']}").json()["status"] == "DISABLED"


def test_integration_api_authz_and_secret_rejection():
    operator = make_client(Role.OPERATOR, {"development-building"})
    building_id = _building_id(operator)
    assert bare_client().get(f"/api/buildings/{building_id}/integrations").status_code == 401
    admin = make_client(Role.ADMIN)
    assert admin.post(f"/api/buildings/{building_id}/integrations", json={
        "name": "admin-write-denied", "integration_type": "CAMERA", "configuration": {},
    }).status_code == 403
    assert admin.get(f"/api/buildings/{building_id}/integrations").status_code == 200
    assert operator.post(f"/api/buildings/{building_id}/integrations", json={
        "name": "unsafe-secret", "integration_type": "CAMERA",
        "configuration": {"rtsp_url": "rtsp://user:password@example.test/cam"},
    }).status_code == 422
    assert operator.post(f"/api/buildings/{building_id}/integrations", json={
        "name": "unsafe-key", "integration_type": "ENERGY_METER",
        "configuration": {"api_key": "do-not-store"},
    }).status_code == 422


def test_operator_building_scope_is_enforced_for_integrations():
    operator = make_client(Role.OPERATOR, {"development-building"})
    organization_id = str(uuid4())
    app.state.organization_repository.add(OrganizationData(
        organization_id, "Other tenant", f"other-{uuid4().hex[:10]}"))
    other = app.state.configuration_repository.create_building(organization_id, {
        "name": "Other building", "slug": f"other-{uuid4().hex[:8]}",
        "building_key": f"{organization_id}:other", "timezone": "UTC", "address": {},
    })
    assert operator.get(f"/api/buildings/{other['building_id']}/integrations").status_code == 403

from datetime import datetime, timezone
from uuid import UUID, uuid4

from backend.api.main import app
from backend.database.models import DeviceRecord, IntegrationRecord, PointMappingRecord
from backend.security.roles import Role
from tests.security_test_utils import bare_client, make_client


def _create_confirmed_temperature_point():
    zone = app.state.configuration_repository.resolve_zone("classroom_01")
    assert zone is not None
    integration_id, device_id, point_id, occupancy_point_id = uuid4(), uuid4(), uuid4(), uuid4()
    with app.state.database_sessions.begin() as session:
        session.add(IntegrationRecord(integration_id=integration_id,
            building_id=UUID(zone.building_id),
            name=f"phase-11-7-{integration_id.hex[:8]}", integration_type="BACNET",
            status="CONFIGURED", configuration={}))
        session.add(DeviceRecord(device_id=device_id, integration_id=integration_id,
            external_device_id=f"device-{device_id.hex[:8]}", name="Simulated controller",
            device_type="HVAC", status="CONFIGURED"))
        session.add(PointMappingRecord(point_mapping_id=point_id, device_id=device_id,
            zone_id=UUID(zone.database_zone_id), external_point_id="AI:3",
            logical_signal="temperature", data_type="number", unit="C", readable=True,
            writable=False, metadata_json={}, mapping_status="CONFIRMED", mapping_source="OPERATOR"))
        session.add(PointMappingRecord(point_mapping_id=occupancy_point_id, device_id=device_id,
            zone_id=UUID(zone.database_zone_id), external_point_id="AI:4",
            logical_signal="occupancy", data_type="integer", unit="people", readable=True,
            writable=False, metadata_json={}, mapping_status="CONFIRMED", mapping_source="OPERATOR"))
    return str(point_id), str(occupancy_point_id)


def test_simulated_observation_api_is_operator_scoped_and_updates_runtime_state():
    point_id, occupancy_point_id = _create_confirmed_temperature_point()
    payload = {"value": 21.7, "observed_at": datetime.now(timezone.utc).isoformat()}
    endpoint = f"/api/point-mappings/{point_id}/simulated-observation"
    occupancy_endpoint = f"/api/point-mappings/{occupancy_point_id}/simulated-observation"

    assert bare_client().post(endpoint, json=payload).status_code == 401
    assert make_client(role=Role.ADMIN).post(endpoint, json=payload).status_code == 403
    assert make_client(building_ids=set()).post(endpoint, json=payload).status_code == 403

    operator = make_client()
    occupancy_result = operator.post(occupancy_endpoint, json={"value": 10,
        "observed_at": datetime.now(timezone.utc).isoformat()})
    assert occupancy_result.status_code == 200 and occupancy_result.json()["runtime_input_applied"] is True
    accepted = operator.post(endpoint, json=payload)
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()
    assert result["accepted"] is True and result["persisted"] is True
    assert result["runtime_input_applied"] is True and result["simulated"] is True

    state_response = operator.get("/api/zones/classroom_01/state")
    assert state_response.status_code == 200
    state = state_response.json()
    assert state["temperature"] == 21.7
    assert state["temperature_source"] == "explicit_value_simulated_provider"
    assert state["temperature_simulated"] is True
    assert state["data_quality"]["signals"]["temperature"]["state"] == "VALID"

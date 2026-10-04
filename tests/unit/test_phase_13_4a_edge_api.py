from backend.api.main import app
from backend.edge.service import EdgeConnectorService
from backend.security.roles import Role
from tests.security_test_utils import bare_client, make_client


def _building_id(client):
    return next(item["building_id"] for item in client.get("/api/buildings").json()["buildings"]
                if item["building_key"] == "development-building")


def _unconfigured_edge():
    return EdgeConnectorService(configuration=app.state.configuration_repository,
        sessions=app.state.database_sessions, adapter_registry=app.state.integration_adapter_registry,
        ingestion_service=app.state.provider_observation_ingestion_service,
        config=None, configuration_error="EDGE_IDENTITY_NOT_CONFIGURED")


def test_edge_status_requires_authentication_and_building_access(monkeypatch):
    monkeypatch.setattr(app.state, "edge_connector", _unconfigured_edge())
    operator = make_client(Role.OPERATOR, {"development-building"})
    building_id = _building_id(operator)
    assert operator.get(f"/api/edge/status/{building_id}").status_code == 200
    assert bare_client().get(f"/api/edge/status/{building_id}").status_code == 401
    unauthorized = make_client(Role.OPERATOR, set())
    assert unauthorized.get(f"/api/edge/status/{building_id}").status_code == 403


def test_edge_status_response_is_read_only_and_clearly_reports_unconfigured_identity(monkeypatch):
    monkeypatch.setattr(app.state, "edge_connector", _unconfigured_edge())
    operator = make_client(Role.OPERATOR, {"development-building"})
    building_id = _building_id(operator)
    status = operator.get(f"/api/edge/status/{building_id}").json()
    assert status["building_id"] == building_id
    assert status["edge_id"] is None
    assert status["healthy"] is False
    assert status["reason_code"] == "EDGE_IDENTITY_NOT_CONFIGURED"
    assert "control" not in status
    assert "credentials" not in status

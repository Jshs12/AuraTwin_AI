from uuid import uuid4

from backend.api.main import app
from backend.edge.service import EdgeConnectorService
from backend.security.roles import Role
from tests.security_test_utils import make_client


def _unconfigured_edge():
    return EdgeConnectorService(configuration=app.state.configuration_repository,
        sessions=app.state.database_sessions, adapter_registry=app.state.integration_adapter_registry,
        ingestion_service=app.state.provider_observation_ingestion_service,
        config=None, configuration_error="EDGE_IDENTITY_NOT_CONFIGURED")


def test_only_assigned_operator_can_request_explicit_observation_retry(monkeypatch):
    monkeypatch.setattr(app.state, "edge_connector", _unconfigured_edge())
    operator = make_client(Role.OPERATOR, {"development-building"})
    building_id = next(item["building_id"] for item in operator.get("/api/buildings").json()["buildings"]
                       if item["building_key"] == "development-building")
    url = f"/api/edge/status/{building_id}/messages/{uuid4()}/retry"
    assert operator.post(url).status_code == 409  # Authorized; edge identity is not configured.
    admin = make_client(Role.ADMIN, {"development-building"})
    assert admin.post(url).status_code == 403  # Oversight role cannot operate the edge.
    other_operator = make_client(Role.OPERATOR, set())
    assert other_operator.post(url).status_code == 403

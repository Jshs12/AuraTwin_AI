from uuid import uuid4

from sqlalchemy import create_engine, func, select

from backend.api.main import app
from backend.database.engine import create_session_factory
from backend.database.models import (Base, BuildingRecord, OrganizationMembershipRecord,
    OrganizationRecord, UserBuildingAccessRecord, UserRecord)
from backend.database.repositories import OrganizationData
from backend.database.repositories import SQLAlchemyUserRepository
from backend.security.passwords import hash_password
from backend.security.roles import Role
from backend.security.service import AuthService
from tests.security_test_utils import bare_client, make_client


def test_auth_service_reconciles_existing_bootstrap_operator_assignment(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'bootstrap.db'}")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    org_id, building_id, user_id = uuid4(), uuid4(), uuid4()
    with sessions.begin() as session:
        session.add(OrganizationRecord(organization_id=org_id, name="Bootstrap Org", slug="bootstrap-org"))
        session.add(BuildingRecord(building_id=building_id, organization_id=org_id,
            name="Development", slug="development", building_key="development-building", address={}))
        session.flush()
        session.add(UserRecord(user_id=user_id, email="operator-bootstrap@example.test",
            email_normalized="operator-bootstrap@example.test", password_hash=hash_password("long-bootstrap-password"),
            role="OPERATOR", active=True))
        session.add(OrganizationMembershipRecord(user_id=user_id, organization_id=org_id))
    monkeypatch.setenv("AURATWIN_BOOTSTRAP_OPERATOR_EMAIL", "operator-bootstrap@example.test")
    monkeypatch.setenv("AURATWIN_BOOTSTRAP_OPERATOR_PASSWORD", "long-bootstrap-password")
    monkeypatch.setenv("AURATWIN_BOOTSTRAP_OPERATOR_BUILDING_ID", "development-building")
    monkeypatch.setenv("AURATWIN_JWT_SECRET", "test-bootstrap-jwt-secret-long-enough-0123456789")
    repository = SQLAlchemyUserRepository(sessions)
    service = AuthService(repository)
    AuthService(repository)
    assert service.authenticate("operator-bootstrap@example.test", "long-bootstrap-password") is not None
    operator = repository.get_by_email("operator-bootstrap@example.test")
    assert operator.role is Role.OPERATOR
    assert operator.organization_ids == frozenset({str(org_id)})
    assert operator.building_ids == frozenset({str(building_id)})
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(UserBuildingAccessRecord)
            .where(UserBuildingAccessRecord.user_id == user_id)) == 1
        assert session.scalar(select(func.count()).select_from(OrganizationMembershipRecord)
            .where(OrganizationMembershipRecord.user_id == user_id)) == 1
    engine.dispose()


def test_persisted_development_operator_can_access_assigned_runtime_and_integrations():
    operator = make_client(Role.OPERATOR, {"development-building"})
    assert operator.get("/api/auth/me").json()["role"] == "OPERATOR"
    buildings = operator.get("/api/buildings")
    assert buildings.status_code == 200
    development = next(item for item in buildings.json()["buildings"]
                       if item["building_key"] == "development-building")
    assert operator.get("/api/zones").status_code == 200
    assert operator.get("/api/monitoring/status").status_code == 200
    for endpoint in ("/api/demo/status", "/api/demo/activity", "/api/demo/events", "/api/demo/building-summary"):
        assert operator.get(endpoint).status_code == 200
    assert operator.get(f"/api/buildings/{development['building_id']}/integrations").status_code == 200
    assert bare_client().get("/api/zones").status_code == 401
    assert bare_client().get("/api/monitoring/status").status_code == 401


def test_operator_scope_and_admin_oversight_are_preserved():
    operator = make_client(Role.OPERATOR, {"development-building"})
    org_id = str(uuid4())
    app.state.organization_repository.add(OrganizationData(org_id, "Foreign scope", f"foreign-{uuid4().hex[:8]}"))
    foreign = app.state.configuration_repository.create_building(org_id, {
        "name": "Unassigned", "slug": "unassigned", "building_key": f"{org_id}:unassigned",
        "timezone": "UTC", "address": {},
    })
    assert operator.get(f"/api/buildings/{foreign['building_id']}/integrations").status_code == 403
    admin = make_client(Role.ADMIN)
    assert admin.get("/api/buildings").status_code == 200
    assert admin.get("/api/zones").status_code == 200
    assert admin.get("/api/monitoring/status").status_code == 200

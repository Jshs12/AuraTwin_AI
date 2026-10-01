from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from backend.api.main import app
from backend.database.configuration import SQLAlchemyConfigurationRepository
from backend.database.engine import create_session_factory
from backend.database.models import (Base, BuildingRecord, OrganizationMembershipRecord,
    OrganizationRecord, UserBuildingAccessRecord, UserRecord)
from backend.knowledge.service import KnowledgeService
from backend.security.audit import AuditService
from backend.security.jwt import create_access_token
from backend.security.models import User
from backend.security.passwords import hash_password
from backend.security.repository import BuildingAccessRepository, InMemoryUserRepository
from backend.security.service import AuthService
from backend.security.roles import Role


@pytest.fixture
def knowledge_api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'knowledge-api.db'}")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    org_a, org_b, building_a, building_b = uuid4(), uuid4(), uuid4(), uuid4()
    operator_id, admin_id = uuid4(), uuid4()
    with sessions.begin() as db:
        db.add_all([
            OrganizationRecord(organization_id=org_a, name="A", slug=f"org-a-{uuid4().hex}"),
            OrganizationRecord(organization_id=org_b, name="B", slug=f"org-b-{uuid4().hex}"),
        ])
        db.flush()
        db.add_all([
            BuildingRecord(building_id=building_a, organization_id=org_a, name="Building A",
                slug=f"a-{uuid4().hex}", building_key=f"building-a-{uuid4().hex}", address={}),
            BuildingRecord(building_id=building_b, organization_id=org_b, name="Building B",
                slug=f"b-{uuid4().hex}", building_key=f"building-b-{uuid4().hex}", address={}),
        ])
        db.flush()
        db.add_all([
            UserRecord(user_id=operator_id, email="knowledge-operator@example.test",
                email_normalized="knowledge-operator@example.test", password_hash=hash_password("operator-password"), role="OPERATOR"),
            UserRecord(user_id=admin_id, email="knowledge-admin@example.test",
                email_normalized="knowledge-admin@example.test", password_hash=hash_password("admin-password"), role="ADMIN"),
        ])
        db.flush()
        db.add(OrganizationMembershipRecord(user_id=operator_id, organization_id=org_a))
        db.add(UserBuildingAccessRecord(user_id=operator_id, building_id=building_a))
    config = SQLAlchemyConfigurationRepository(sessions)
    audit = AuditService()
    monkeypatch.setattr(app.state, "database_sessions", sessions)
    monkeypatch.setattr(app.state, "configuration_repository", config)
    monkeypatch.setattr(app.state, "building_access", BuildingAccessRepository(config))
    monkeypatch.setattr(app.state, "audit_service", audit)
    monkeypatch.setattr(app.state, "knowledge_service", KnowledgeService(sessions), raising=False)

    operator = User(str(operator_id), "knowledge-operator@example.test", "x", Role.OPERATOR,
        building_ids=frozenset({str(building_a)}), organization_ids=frozenset({str(org_a)}))
    admin = User(str(admin_id), "knowledge-admin@example.test", "x", Role.ADMIN)
    users = InMemoryUserRepository()
    users.add(operator)
    users.add(admin)
    monkeypatch.setattr(app.state, "auth_service", AuthService(users))
    operator_token, _ = create_access_token(operator.user_id, Role.OPERATOR)
    admin_token, _ = create_access_token(admin.user_id, Role.ADMIN)
    yield {"operator": TestClient(app, headers={"Authorization": f"Bearer {operator_token}"}),
        "admin": TestClient(app, headers={"Authorization": f"Bearer {admin_token}"}),
        "audit": audit, "building_a": str(building_a), "building_b": str(building_b),
        "engine": engine}
    engine.dispose()


def test_operator_upload_ingest_retrieve_and_audit(knowledge_api):
    ctx = knowledge_api
    root = f"/api/buildings/{ctx['building_a']}/knowledge"
    registered = ctx["operator"].post(f"{root}/documents", data={
        "name": "Cooling policy", "category": "OPERATING_POLICY", "source_reference": "Policy rev 4",
    }, files={"file": ("cooling.md", b"# Cooling\nOccupied rooms use the building comfort policy.", "text/markdown")})
    assert registered.status_code == 201, registered.text
    document = registered.json()
    assert document["ingestion_status"] == "REGISTERED"
    ingested = ctx["operator"].post(f"{root}/documents/{document['document_id']}/ingest")
    assert ingested.status_code == 200, ingested.text
    assert ingested.json()["ingestion_status"] == "READY"
    response = ctx["operator"].post(f"{root}/retrieve", json={"query": "occupied comfort policy"})
    assert response.status_code == 200
    assert response.json()["semantic_search"] is False
    result = response.json()["results"][0]
    assert result["document_name"] == "Cooling policy"
    assert result["source"] == "Policy rev 4"
    assert result["section"] == "Cooling"
    actions = [row.action for row in ctx["audit"].list_records()]
    assert "knowledge_document_registered" in actions
    assert "knowledge_document_ingestion_started" in actions
    assert "knowledge_document_ingestion_succeeded" in actions
    assert "knowledge_retrieved" in actions


def test_cross_building_retrieval_and_version_write_are_denied_and_audited(knowledge_api):
    ctx = knowledge_api
    root_a = f"/api/buildings/{ctx['building_a']}/knowledge"
    created = ctx["operator"].post(f"{root_a}/documents", data={
        "name": "Policy", "category": "OTHER",
    }, files={"file": ("p.txt", b"Cooling policy", "text/plain")}).json()
    root_b = f"/api/buildings/{ctx['building_b']}/knowledge"
    denied_list = ctx["operator"].get(f"{root_b}/documents")
    denied_search = ctx["operator"].post(f"{root_b}/retrieve", json={"query": "cooling policy"})
    denied_version = ctx["operator"].post(f"{root_b}/documents/{created['document_id']}/versions",
        files={"file": ("other.txt", b"other content", "text/plain")})
    assert [denied_list.status_code, denied_search.status_code, denied_version.status_code] == [403, 403, 403]
    denied = [row for row in ctx["audit"].list_records() if row.action == "knowledge_access_denied"]
    assert len(denied) == 3
    assert all(not row.success for row in denied)
    anonymous = TestClient(app).post(f"{root_a}/retrieve", json={"query": "cooling policy"})
    assert anonymous.status_code == 401
    assert any(row.action == "knowledge_access_denied" and
        row.metadata.get("reason_code") == "AUTHENTICATION_REQUIRED"
        for row in ctx["audit"].list_records())


def test_admin_can_read_building_knowledge_but_cannot_operate_documents(knowledge_api):
    ctx = knowledge_api
    path = f"/api/buildings/{ctx['building_a']}/knowledge/documents"
    assert ctx["admin"].get(path).status_code == 200
    denied = ctx["admin"].post(path, data={"name": "Not allowed", "category": "OTHER"},
        files={"file": ("a.txt", b"text", "text/plain")})
    assert denied.status_code == 403

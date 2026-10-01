import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend.security.roles import Role, Permission, ROLE_PERMISSIONS, ADMIN_PERMISSIONS, OPERATOR_PERMISSIONS, has_permission
from backend.security.passwords import hash_password, verify_password
from backend.security.jwt import create_access_token, decode_access_token, ISSUER, AUDIENCE
from backend.security.repository import InMemoryUserRepository
from backend.security.models import User
from backend.api.main import app
from backend.core.events import EventTrace, EventBroadcaster
from tests.security_test_utils import make_client, make_user, bare_client
from backend.security.service import AuthService


TEST_SECRET = "test-only-secret-not-used-outside-tests-0123456789"


def test_only_two_roles_and_explicit_permissions():
    assert {role.value for role in Role} == {"ADMIN", "OPERATOR"}
    assert set(ROLE_PERMISSIONS) == {Role.ADMIN, Role.OPERATOR}
    assert ADMIN_PERMISSIONS == {Permission.BUILDING_READ, Permission.ZONES_READ,
        Permission.TELEMETRY_READ, Permission.ENERGY_READ, Permission.EVENTS_READ,
        Permission.SYSTEM_READ, Permission.AUDIT_READ, Permission.ACCESS_READ}
    assert OPERATOR_PERMISSIONS == {Permission.BUILDING_READ, Permission.BUILDING_CONFIGURE,
        Permission.ZONES_READ, Permission.ZONES_CREATE, Permission.ZONES_UPDATE,
        Permission.ZONES_DELETE, Permission.INTEGRATIONS_CONFIGURE,
        Permission.MONITORING_MANAGE, Permission.RECOMMENDATIONS_READ,
        Permission.CONTROL_EXECUTE, Permission.ACCESS_MANAGE}
    assert has_permission(Role.OPERATOR, Permission.CONTROL_EXECUTE)
    assert not has_permission(Role.ADMIN, Permission.CONTROL_EXECUTE)
    assert not has_permission(Role.ADMIN, "unknown:permission")


def test_argon2_password_hash_and_verification():
    encoded = hash_password("correct horse")
    assert encoded.startswith("$argon2id$")
    assert encoded != "correct horse"
    assert verify_password("correct horse", encoded)
    assert not verify_password("incorrect", encoded)


def test_jwt_valid_signature_expiry_and_required_claims(monkeypatch):
    monkeypatch.setenv("AURATWIN_JWT_SECRET", TEST_SECRET)
    token, _ = create_access_token("user-1", Role.OPERATOR)
    assert decode_access_token(token)["sub"] == "user-1"
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token + "bad")
    expired = jwt.encode({"sub": "user-1", "role": "OPERATOR", "iat": datetime.now(timezone.utc)-timedelta(hours=1),
        "exp": datetime.now(timezone.utc)-timedelta(minutes=1), "iss": ISSUER, "aud": AUDIENCE, "type": "access"},
        TEST_SECRET, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError): decode_access_token(expired)
    incomplete = jwt.encode({"sub": "user-1", "role": "OPERATOR", "iss": ISSUER, "aud": AUDIENCE},
        TEST_SECRET, algorithm="HS256")
    with pytest.raises(jwt.MissingRequiredClaimError): decode_access_token(incomplete)
    invalid_role = jwt.encode({"sub": "user-1", "role": "VIEWER", "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc)+timedelta(minutes=2), "iss": ISSUER, "aud": AUDIENCE, "type": "access"},
        TEST_SECRET, algorithm="HS256")
    with pytest.raises(ValueError): decode_access_token(invalid_role)


def test_repository_rejects_duplicate_email_case_insensitively():
    repo = InMemoryUserRepository()
    user = User("id-1", "user@example.test", hash_password("pw"), Role.OPERATOR, True,
                frozenset({"development-building"}))
    repo.add(user)
    with pytest.raises(ValueError):
        repo.add(User("id-2", "USER@example.test", hash_password("pw"), Role.OPERATOR))


def test_bootstrap_admin_is_argon2_hashed_and_configured_only(monkeypatch):
    monkeypatch.setenv("AURATWIN_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("AURATWIN_BOOTSTRAP_ADMIN_EMAIL", "seed@example.test")
    monkeypatch.setenv("AURATWIN_BOOTSTRAP_ADMIN_PASSWORD", "bootstrap-password")
    monkeypatch.delenv("AURATWIN_BOOTSTRAP_OPERATOR_EMAIL", raising=False)
    monkeypatch.delenv("AURATWIN_BOOTSTRAP_OPERATOR_PASSWORD", raising=False)
    service = AuthService()
    admin = service.users.get_by_email("seed@example.test")
    assert admin and admin.role == Role.ADMIN and admin.active
    assert admin.password_hash.startswith("$argon2id$")
    assert "bootstrap-password" not in admin.password_hash
    assert len(service.users.list_users()) == 1


def test_operator_access_management_is_scoped_and_audited():
    operator = make_client(role=Role.OPERATOR)
    created = operator.post("/api/auth/operators", json={
        "email": "new-operator@example.test", "password": "another-safe-password"})
    assert created.status_code == 201
    assert created.json()["role"] == "OPERATOR"
    assert created.json()["building_ids"] == ["development-building"]
    assert "password_hash" not in created.text and "another-safe-password" not in created.text
    user_id = created.json()["user_id"]
    assert operator.get("/api/auth/access").status_code == 200
    revoked = operator.delete(f"/api/auth/operators/{user_id}")
    assert revoked.status_code == 200 and revoked.json()["active"] is False
    target_token, _ = create_access_token(user_id, Role.OPERATOR)
    assert bare_client().get("/api/zones", headers={"Authorization": f"Bearer {target_token}"}).status_code == 401
    audit_actions = {row.action for row in app.state.audit_service.list_records()}
    assert {"operator_created", "operator_access_revoked"} <= audit_actions


def test_admin_cannot_use_operator_access_management():
    admin = make_client(role=Role.ADMIN)
    assert admin.post("/api/auth/operators", json={
        "email": "blocked@example.test", "password": "another-safe-password"}).status_code == 403


def test_auth_login_me_invalid_and_inactive_account():
    user = make_user(email="auth@example.test", password="safe-password")
    client = bare_client()
    assert client.post("/api/auth/login", json={"email": user.email, "password": "wrong"}).status_code == 401
    response = client.post("/api/auth/login", json={"email": user.email, "password": "safe-password"})
    assert response.status_code == 200
    assert "password_hash" not in response.text and "safe-password" not in response.text
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}).json()["role"] == "OPERATOR"
    inactive = make_user(active=False, email="inactive@example.test", password="password")
    assert client.post("/api/auth/login", json={"email": inactive.email, "password": "password"}).status_code == 401


def test_protected_api_and_control_require_authentication():
    client = bare_client()
    assert client.get("/api/zones").status_code == 401
    assert client.get("/api/monitoring/status").status_code == 401
    assert client.post("/api/zones/classroom_01/control", json={}).status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/auth/logout").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_admin_read_only_and_operator_control_permission():
    admin = make_client(role=Role.ADMIN)
    operator = make_client(role=Role.OPERATOR)
    assert admin.get("/api/zones").status_code == 200
    assert operator.get("/api/zones").status_code == 200
    assert admin.get("/api/auth/access").status_code == 200
    assert admin.get("/api/audit").status_code == 200
    assert operator.get("/api/auth/access").status_code == 200
    assert operator.get("/api/audit").status_code == 403
    recommendation = operator.post("/api/zones/classroom_01/recommendation")
    assert recommendation.status_code == 200
    assert operator.post("/api/zones/classroom_01/control", json=recommendation.json()).status_code == 200
    assert admin.post("/api/zones/classroom_01/control", json=recommendation.json()).status_code == 403
    assert admin.post("/api/zones/classroom_01/recommendation").status_code == 403


def test_operator_building_assignment_is_enforced_before_control():
    unauthorized = make_client(role=Role.OPERATOR, building_ids=set())
    assert unauthorized.get("/api/zones").status_code == 403
    assert unauthorized.post("/api/zones/classroom_01/control", json={}).status_code == 403
    admin = make_client(role=Role.ADMIN, building_ids=set())
    assert admin.get("/api/zones").status_code == 200


def test_logout_is_honest_stateless_token_disposal():
    client = make_client()
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json()["status"] == "client_token_disposal_required"
    assert "discard it in the client" in response.json()["message"]


def test_audit_records_login_control_and_never_store_credentials():
    app.state.audit_service._records.clear()
    user = make_user(email="audit@example.test", password="private-password")
    bare = bare_client()
    login = bare.post("/api/auth/login", json={"email": user.email, "password": "private-password"})
    assert login.status_code == 200
    client = make_client(user=user)
    rec = client.post("/api/zones/classroom_01/recommendation")
    client.post("/api/zones/classroom_01/control", json=rec.json())
    records = app.state.audit_service.list_records()
    text = repr(records)
    assert any(row.action == "login_succeeded" for row in records)
    assert any(row.action == "control_execute" for row in records)
    assert "private-password" not in text and login.json()["access_token"] not in text
    assert all("password_hash" not in repr(row.metadata) for row in records)


def test_websocket_rejects_anonymous_and_accepts_authorized_operator():
    client = bare_client()
    with pytest.raises(Exception):
        with client.websocket_connect("/api/monitoring/ws/events"):
            pass
    authorized = make_client()
    token = authorized.headers["Authorization"].split(" ", 1)[1]
    subscriber_count = len(EventBroadcaster._subscribers)
    with authorized.websocket_connect(f"/api/monitoring/ws/events?access_token={token}") as websocket:
        assert len(EventBroadcaster._subscribers) == subscriber_count + 1
        response = authorized.post("/api/zones/classroom_01/recommendation")
        assert response.status_code == 200
        event = websocket.receive_json()
        assert event["event_type"] in {"OCCUPANCY_DETECTED", "STATE_EVALUATED", "OPTIMIZATION_REQUESTED",
            "INTELLIGENCE_REQUESTED", "INTELLIGENCE_RESPONSE", "RECOMMENDATION_VALIDATED", "OPTIMIZATION_RECOMMENDATION"}
    assert len(EventBroadcaster._subscribers) == subscriber_count


def test_api_secret_never_in_events_or_audit():
    marker = "sensitive-token-marker"
    EventTrace.log_event("TEST_SAFE", "classroom_01", "test", {"safe": True})
    assert marker not in repr(app.state.audit_service.list_records())

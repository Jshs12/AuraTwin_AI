from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend.core.events import EventBroadcaster
from backend.security.jwt import AUDIENCE, ISSUER
from backend.security.roles import Role
from tests.security_test_utils import bare_client, make_client


PROTECTED_DEMO_ROUTES = (
    "/api/monitoring/status",
    "/api/demo/building-summary",
    "/api/demo/events",
)


@pytest.mark.parametrize("path", PROTECTED_DEMO_ROUTES)
def test_runtime_status_and_demo_routes_require_a_valid_operator_session(path):
    assert bare_client().get(path).status_code == 401
    assert bare_client().get(path, headers={"Authorization": "Bearer not-a-valid-token"}).status_code == 401
    operator = make_client(Role.OPERATOR)
    assert operator.get(path).status_code == 200


@pytest.mark.parametrize("path", PROTECTED_DEMO_ROUTES)
def test_runtime_status_and_demo_routes_reject_expired_tokens(path):
    token = jwt.encode({
        "sub": "expired-user", "role": "OPERATOR",
        "iat": datetime.now(timezone.utc) - timedelta(hours=1),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        "iss": ISSUER, "aud": AUDIENCE, "type": "access",
    }, "test-only-secret-not-used-outside-tests-0123456789", algorithm="HS256")
    response = bare_client().get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "Bearer" in response.headers.get("www-authenticate", "")


def test_authenticated_monitoring_websocket_accepts_operator_and_cleans_up():
    operator = make_client(Role.OPERATOR)
    token = operator.headers["Authorization"].split(" ", 1)[1]
    before = len(EventBroadcaster._subscribers)
    with operator.websocket_connect(f"/api/monitoring/ws/events?access_token={token}") as socket:
        assert len(EventBroadcaster._subscribers) == before + 1
    assert len(EventBroadcaster._subscribers) == before


def test_admin_remains_denied_from_operator_runtime_control():
    admin = make_client(Role.ADMIN)
    assert admin.post("/api/monitoring/start").status_code == 403
    assert admin.post("/api/zones/classroom_01/control-enabled", json={"enabled": False}).status_code == 403
    assert admin.post("/api/zones/classroom_01/manual-override", json={"enabled": True}).status_code == 403

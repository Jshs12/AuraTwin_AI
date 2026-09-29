"""Test-only identity helpers. Never used by the application runtime."""
import os
import uuid
os.environ["AURATWIN_JWT_SECRET"] = "test-only-secret-not-used-outside-tests-0123456789"

from fastapi.testclient import TestClient
from backend.api.main import app
from backend.security.models import User
from backend.security.roles import Role
from backend.security.passwords import hash_password
from backend.security.jwt import create_access_token


def make_user(role=Role.OPERATOR, building_ids=frozenset({"development-building"}),
              active=True, email=None, password="test-password"):
    user = User(str(uuid.uuid4()), email or f"{uuid.uuid4().hex}@example.test",
                hash_password(password), role, active, frozenset(building_ids))
    app.state.auth_service.users.add(user)
    return user


def make_client(role=Role.OPERATOR, building_ids=frozenset({"development-building"}),
                active=True, user=None):
    user = user or make_user(role, building_ids, active)
    token, _ = create_access_token(user.user_id, user.role)
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def bare_client():
    return TestClient(app)

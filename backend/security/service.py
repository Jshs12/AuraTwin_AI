import os
import uuid
from .models import User
from .roles import Role
from .repository import InMemoryUserRepository, DEVELOPMENT_BUILDING_ID
from .passwords import hash_password, verify_password
from .jwt import create_access_token
from .jwt import token_settings


class AuthService:
    def __init__(self, users: InMemoryUserRepository | None = None):
        self.users = users or InMemoryUserRepository()
        email, password = os.getenv("AURATWIN_BOOTSTRAP_ADMIN_EMAIL"), os.getenv("AURATWIN_BOOTSTRAP_ADMIN_PASSWORD")
        operator_email = os.getenv("AURATWIN_BOOTSTRAP_OPERATOR_EMAIL")
        operator_password = os.getenv("AURATWIN_BOOTSTRAP_OPERATOR_PASSWORD")
        if bool(email) != bool(password) or bool(operator_email) != bool(operator_password):
            raise RuntimeError("Bootstrap account email and password must be configured together")
        if (password and len(password) < 12) or (operator_password and len(operator_password) < 12):
            raise RuntimeError("Bootstrap passwords must be at least 12 characters")
        if (email and password) or (operator_email and operator_password):
            token_settings()  # Fail startup clearly when enabled authentication lacks a strong signing secret.
        if email and password and not self.users.get_by_email(email):
            self.users.add(User(str(uuid.uuid4()), email.strip().lower(), hash_password(password),
                                Role.ADMIN, True, frozenset()))
        if operator_email and operator_password and not self.users.get_by_email(operator_email):
            building_id = os.getenv("AURATWIN_BOOTSTRAP_OPERATOR_BUILDING_ID", DEVELOPMENT_BUILDING_ID)
            self.users.add(User(str(uuid.uuid4()), operator_email.strip().lower(), hash_password(operator_password),
                                Role.OPERATOR, True, frozenset({building_id})))

    def authenticate(self, email: str, password: str):
        user = self.users.get_by_email(email.strip().lower())
        if user is None or not user.active or not verify_password(password, user.password_hash): return None
        token, expires = create_access_token(user.user_id, user.role)
        return token, expires, user

    @staticmethod
    def safe_user(user: User):
        return {"user_id": user.user_id, "email": user.email, "role": user.role,
                "active": user.active, "building_ids": sorted(user.building_ids)}

from threading import RLock
from dataclasses import replace
from .models import User

DEVELOPMENT_BUILDING_ID = "development-building"


class UserRepository:
    """Repository boundary; replace this adapter with persistent storage in Phase 11."""
    def get_by_email(self, email: str) -> User | None: raise NotImplementedError
    def get_by_id(self, user_id: str) -> User | None: raise NotImplementedError
    def add(self, user: User) -> None: raise NotImplementedError
    def list_users(self) -> list[User]: raise NotImplementedError
    def deactivate(self, user_id: str) -> User | None: raise NotImplementedError


class InMemoryUserRepository(UserRepository):
    def __init__(self):
        self._users: dict[str, User] = {}
        self._lock = RLock()

    def get_by_email(self, email):
        with self._lock: return next((u for u in self._users.values() if u.email.casefold() == email.casefold()), None)
    def get_by_id(self, user_id):
        with self._lock: return self._users.get(user_id)
    def add(self, user):
        with self._lock:
            if self.get_by_email(user.email) or user.user_id in self._users: raise ValueError("User already exists")
            self._users[user.user_id] = user
    def list_users(self):
        with self._lock: return list(self._users.values())

    def deactivate(self, user_id: str) -> User | None:
        with self._lock:
            user = self._users.get(user_id)
            if user is None: return None
            updated = replace(user, active=False)
            self._users[user_id] = updated
            return updated


class BuildingAccessRepository:
    def __init__(self, configuration_repository=None):
        self.configuration_repository = configuration_repository

    def has_access(self, user: User, building_id: str) -> bool:
        if user.role.value == "ADMIN" or building_id in user.building_ids:
            return True
        if self.configuration_repository is None:
            return False
        requested = self.configuration_repository.resolve_building_id(building_id)
        if requested is None:
            return False
        return any(self.configuration_repository.resolve_building_id(assigned) == requested
                   for assigned in user.building_ids)

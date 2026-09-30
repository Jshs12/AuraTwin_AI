"""Persistence ports and SQLAlchemy adapters for Phase 11 foundation records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.security.models import User
from backend.security.repository import UserRepository
from backend.security.roles import Role
from backend.database.models import (BuildingRecord, OrganizationRecord,
                                     UserBuildingAccessRecord, UserRecord)


@dataclass(frozen=True)
class OrganizationData:
    organization_id: str
    name: str
    slug: str


@dataclass(frozen=True)
class BuildingData:
    building_id: str
    organization_id: str
    name: str
    slug: str
    timezone: str
    address: dict
    archived_at: datetime | None = None


class OrganizationRepository(Protocol):
    def add(self, organization: OrganizationData) -> None: ...
    def get(self, organization_id: str) -> OrganizationData | None: ...


class BuildingRepository(Protocol):
    def add(self, building: BuildingData) -> None: ...
    def get(self, building_id: str) -> BuildingData | None: ...
    def list_for_organization(self, organization_id: str) -> list[BuildingData]: ...


class SQLAlchemyOrganizationRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def add(self, organization: OrganizationData) -> None:
        with self.sessions.begin() as session:
            session.add(OrganizationRecord(organization_id=UUID(organization.organization_id),
                name=organization.name, slug=organization.slug))

    def get(self, organization_id: str) -> OrganizationData | None:
        with self.sessions() as session:
            row = session.get(OrganizationRecord, UUID(organization_id))
            return OrganizationData(str(row.organization_id), row.name, row.slug) if row else None


class SQLAlchemyBuildingRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def add(self, building: BuildingData) -> None:
        with self.sessions.begin() as session:
            session.add(BuildingRecord(building_id=UUID(building.building_id),
                organization_id=UUID(building.organization_id), name=building.name,
                slug=building.slug, timezone=building.timezone, address=building.address,
                archived_at=building.archived_at))

    def get(self, building_id: str) -> BuildingData | None:
        with self.sessions() as session:
            row = session.get(BuildingRecord, UUID(building_id))
            return self._data(row) if row else None

    def list_for_organization(self, organization_id: str) -> list[BuildingData]:
        with self.sessions() as session:
            rows = session.scalars(select(BuildingRecord)
                .where(BuildingRecord.organization_id == UUID(organization_id))
                .order_by(BuildingRecord.name)).all()
            return [self._data(row) for row in rows]

    @staticmethod
    def _data(row: BuildingRecord) -> BuildingData:
        return BuildingData(str(row.building_id), str(row.organization_id), row.name,
            row.slug, row.timezone, dict(row.address or {}), row.archived_at)


class SQLAlchemyUserRepository(UserRepository):
    """Auth repository adapter; the FastAPI app continues to use memory until a later cutover."""

    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def get_by_email(self, email: str) -> User | None:
        with self.sessions() as session:
            row = session.scalar(select(UserRecord).where(UserRecord.email_normalized == email.strip().lower()))
            return self._user(row) if row else None

    def get_by_id(self, user_id: str) -> User | None:
        try:
            identifier = UUID(user_id)
        except (ValueError, TypeError):
            return None
        with self.sessions() as session:
            row = session.get(UserRecord, identifier)
            return self._user(row) if row else None

    def add(self, user: User) -> None:
        with self.sessions.begin() as session:
            row = UserRecord(user_id=UUID(user.user_id), email=user.email.strip(),
                email_normalized=user.email.strip().lower(),
                password_hash=user.password_hash, role=user.role.value, active=user.active)
            row.building_access = [UserBuildingAccessRecord(building_id=UUID(building_id))
                                   for building_id in user.building_ids]
            session.add(row)

    def list_users(self) -> list[User]:
        with self.sessions() as session:
            return [self._user(row) for row in session.scalars(select(UserRecord).order_by(UserRecord.email)).all()]

    def deactivate(self, user_id: str) -> User | None:
        try:
            identifier = UUID(user_id)
        except (ValueError, TypeError):
            return None
        with self.sessions.begin() as session:
            row = session.get(UserRecord, identifier)
            if row is None:
                return None
            row.active = False
            session.flush()
            return self._user(row)

    @staticmethod
    def _user(row: UserRecord) -> User:
        building_ids = frozenset(str(assignment.building_id) for assignment in row.building_access)
        return User(str(row.user_id), row.email, row.password_hash, Role(row.role), row.active, building_ids)

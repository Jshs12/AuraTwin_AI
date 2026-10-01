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
from backend.database.models import (BuildingRecord, OrganizationMembershipRecord, OrganizationRecord,
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
    building_key: str = ""
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
                slug=building.slug, building_key=building.building_key or building.slug,
                timezone=building.timezone, address=building.address,
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
            row.slug, row.timezone, dict(row.address or {}), row.building_key, row.archived_at)


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
            organizations = {UUID(org_id) for org_id in user.organization_ids}
            for building_identifier in user.building_ids:
                try:
                    building_uuid = UUID(building_identifier)
                    building = session.get(BuildingRecord, building_uuid)
                except ValueError:
                    building = session.scalar(select(BuildingRecord).where(
                        BuildingRecord.building_key == building_identifier))
                    building_uuid = building.building_id if building else None
                if building is None or building_uuid is None or building.archived_at is not None:
                    raise ValueError("Building access assignment is invalid")
                row.building_access.append(UserBuildingAccessRecord(building_id=building_uuid))
                organizations.add(building.organization_id)
            row.organization_memberships = [OrganizationMembershipRecord(organization_id=organization_id)
                                            for organization_id in organizations]
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
        organization_ids = frozenset(str(item.organization_id) for item in row.organization_memberships)
        return User(str(row.user_id), row.email, row.password_hash, Role(row.role), row.active,
                    building_ids, organization_ids)

"""Repository-backed organization/building/floor/zone configuration boundary."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from backend.core.time import utc_now
from backend.database.models import (BuildingRecord, FloorRecord, OrganizationMembershipRecord,
    OrganizationRecord, UserBuildingAccessRecord, ZoneRecord)
from backend.security.models import User
from backend.security.roles import Role


@dataclass(frozen=True)
class ZoneConfiguration:
    zone_id: str
    database_zone_id: str
    zone_key: str
    building_id: str
    building_key: str
    floor_id: str
    name: str
    type: str
    capacity: int
    area_m2: float
    comfort: dict[str, float]
    archived_at: datetime | None = None

    def api_dict(self) -> dict[str, Any]:
        return asdict(self)


class SQLAlchemyConfigurationRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    @staticmethod
    def _uuid(value: str) -> UUID | None:
        try:
            return UUID(value)
        except (ValueError, TypeError, AttributeError):
            return None

    def resolve_building(self, identifier: str, *, include_archived: bool = False) -> BuildingRecord | None:
        with self.sessions() as session:
            row = self._building(session, identifier)
            if row is None or (row.archived_at is not None and not include_archived):
                return None
            return self._building_view(row)

    def _building(self, session: Session, identifier: str) -> BuildingRecord | None:
        parsed = self._uuid(identifier)
        if parsed:
            row = session.get(BuildingRecord, parsed)
            if row is not None:
                return row
        return session.scalar(select(BuildingRecord).where(BuildingRecord.building_key == identifier))

    @staticmethod
    def _building_view(row: BuildingRecord) -> BuildingRecord:
        # ORM objects are detached when returned; all consumed scalar fields are loaded.
        return row

    def resolve_building_id(self, identifier: str) -> str | None:
        row = self.resolve_building(identifier)
        return str(row.building_id) if row else None

    def list_organizations(self, user: User) -> list[dict]:
        with self.sessions() as session:
            query = select(OrganizationRecord).where(OrganizationRecord.archived_at.is_(None))
            if user.role != Role.ADMIN:
                user_id = self._uuid(user.user_id)
                if user_id is None:
                    return []
                query = query.join(OrganizationMembershipRecord).where(
                    OrganizationMembershipRecord.user_id == user_id)
            return [self._organization_dict(row) for row in session.scalars(query.order_by(OrganizationRecord.name)).all()]

    def get_organization(self, identifier: str, *, include_archived: bool = False) -> dict | None:
        organization_id = self._uuid(identifier)
        if organization_id is None:
            return None
        with self.sessions() as session:
            row = session.get(OrganizationRecord, organization_id)
            if row is None or (row.archived_at is not None and not include_archived):
                return None
            return self._organization_dict(row)

    @staticmethod
    def _organization_dict(row: OrganizationRecord) -> dict:
        return {"organization_id": str(row.organization_id), "name": row.name,
                "slug": row.slug, "archived_at": row.archived_at}

    def list_buildings(self, user: User, organization_id: str | None = None) -> list[BuildingRecord]:
        with self.sessions() as session:
            query = select(BuildingRecord).where(BuildingRecord.archived_at.is_(None))
            if organization_id:
                org_uuid = self._uuid(organization_id)
                if org_uuid is None:
                    return []
                query = query.where(BuildingRecord.organization_id == org_uuid)
            if user.role != Role.ADMIN:
                user_uuid = self._uuid(user.user_id)
                if user_uuid is None:
                    return []
                query = query.join(UserBuildingAccessRecord).where(UserBuildingAccessRecord.user_id == user_uuid)
            return list(session.scalars(query.order_by(BuildingRecord.name)).all())

    def list_building_dicts(self, user: User, organization_id: str | None = None) -> list[dict]:
        return [self._building_dict(row) for row in self.list_buildings(user, organization_id)]

    def get_building(self, identifier: str, *, include_archived: bool = False) -> dict | None:
        with self.sessions() as session:
            row = self._building(session, identifier)
            if row is None or (row.archived_at is not None and not include_archived):
                return None
            return self._building_dict(row)

    @staticmethod
    def _building_dict(row: BuildingRecord) -> dict:
        return {"building_id": str(row.building_id), "building_key": row.building_key,
            "organization_id": str(row.organization_id), "name": row.name, "slug": row.slug,
            "timezone": row.timezone, "address": dict(row.address or {}), "archived_at": row.archived_at,
            "created_at": row.created_at, "updated_at": row.updated_at}

    def organization_for_building(self, building_id: str) -> str | None:
        row = self.resolve_building(building_id)
        return str(row.organization_id) if row else None

    def user_has_organization(self, user: User, organization_id: str) -> bool:
        if user.role == Role.ADMIN:
            return self.get_organization(organization_id) is not None
        user_id, org_id = self._uuid(user.user_id), self._uuid(organization_id)
        if user_id is None or org_id is None:
            return False
        with self.sessions() as session:
            return session.scalar(select(OrganizationMembershipRecord.membership_id).where(
                OrganizationMembershipRecord.user_id == user_id,
                OrganizationMembershipRecord.organization_id == org_id)) is not None

    def create_building(self, organization_id: str, values: dict) -> dict:
        org_id = self._uuid(organization_id)
        if org_id is None:
            raise ValueError("Organization not found")
        row = BuildingRecord(building_id=uuid4(), organization_id=org_id, **values)
        with self.sessions.begin() as session:
            if session.get(OrganizationRecord, org_id) is None:
                raise ValueError("Organization not found")
            session.add(row)
            session.flush()
            return self._building_dict(row)

    def assign_building_access(self, user_id: str, building_id: str) -> None:
        user_uuid, building_uuid = self._uuid(user_id), self.resolve_building_id(building_id)
        if user_uuid is None or building_uuid is None:
            raise ValueError("User or building not found")
        with self.sessions.begin() as session:
            exists = session.scalar(select(UserBuildingAccessRecord.assignment_id).where(
                UserBuildingAccessRecord.user_id == user_uuid,
                UserBuildingAccessRecord.building_id == UUID(building_uuid)))
            if exists is None:
                session.add(UserBuildingAccessRecord(user_id=user_uuid, building_id=UUID(building_uuid)))

    def update_building(self, identifier: str, values: dict) -> dict | None:
        with self.sessions.begin() as session:
            row = self._building(session, identifier)
            if row is None or row.archived_at is not None:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = utc_now()
            session.flush()
            return self._building_dict(row)

    def archive_building(self, identifier: str) -> dict | None:
        with self.sessions.begin() as session:
            row = self._building(session, identifier)
            if row is None:
                return None
            if row.archived_at is None:
                row.archived_at = utc_now()
                row.updated_at = row.archived_at
            return self._building_dict(row)

    def list_floors(self, building_id: str, *, include_archived: bool = False) -> list[dict]:
        building = self.resolve_building(building_id)
        if building is None:
            return []
        with self.sessions() as session:
            query = select(FloorRecord).where(FloorRecord.building_id == building.building_id)
            if not include_archived:
                query = query.where(FloorRecord.archived_at.is_(None))
            return [self._floor_dict(row) for row in session.scalars(query.order_by(FloorRecord.level_number, FloorRecord.name)).all()]

    def get_floor(self, floor_id: str, *, include_archived: bool = False) -> dict | None:
        identifier = self._uuid(floor_id)
        if identifier is None:
            return None
        with self.sessions() as session:
            row = session.get(FloorRecord, identifier)
            if row is None or (row.archived_at is not None and not include_archived):
                return None
            return self._floor_dict(row)

    @staticmethod
    def _floor_dict(row: FloorRecord) -> dict:
        return {"floor_id": str(row.floor_id), "building_id": str(row.building_id),
            "name": row.name, "floor_key": row.floor_key, "level_number": row.level_number,
            "archived_at": row.archived_at, "created_at": row.created_at, "updated_at": row.updated_at}

    def create_floor(self, building_id: str, values: dict) -> dict:
        building = self.resolve_building(building_id)
        if building is None:
            raise ValueError("Building not found")
        row = FloorRecord(floor_id=uuid4(), building_id=building.building_id, **values)
        with self.sessions.begin() as session:
            session.add(row)
            session.flush()
            return self._floor_dict(row)

    def update_floor(self, floor_id: str, values: dict) -> dict | None:
        identifier = self._uuid(floor_id)
        if identifier is None:
            return None
        with self.sessions.begin() as session:
            row = session.get(FloorRecord, identifier)
            if row is None or row.archived_at is not None:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = utc_now()
            session.flush()
            return self._floor_dict(row)

    def archive_floor(self, floor_id: str) -> dict | None:
        identifier = self._uuid(floor_id)
        if identifier is None:
            return None
        with self.sessions.begin() as session:
            row = session.get(FloorRecord, identifier)
            if row is None:
                return None
            if row.archived_at is None:
                row.archived_at = utc_now()
                row.updated_at = row.archived_at
            return self._floor_dict(row)

    def list_zones(self, *, building_id: str | None = None, floor_id: str | None = None,
                   include_archived: bool = False) -> list[ZoneConfiguration]:
        with self.sessions() as session:
            query = (select(ZoneRecord, FloorRecord, BuildingRecord)
                     .join(FloorRecord, ZoneRecord.floor_id == FloorRecord.floor_id)
                     .join(BuildingRecord, FloorRecord.building_id == BuildingRecord.building_id))
            if building_id:
                building = self._building(session, building_id)
                if building is None:
                    return []
                query = query.where(BuildingRecord.building_id == building.building_id)
            if floor_id:
                floor_uuid = self._uuid(floor_id)
                if floor_uuid is None:
                    return []
                query = query.where(FloorRecord.floor_id == floor_uuid)
            if not include_archived:
                query = query.where(ZoneRecord.archived_at.is_(None), FloorRecord.archived_at.is_(None),
                                    BuildingRecord.archived_at.is_(None))
            rows = session.execute(query.order_by(BuildingRecord.name, FloorRecord.level_number, ZoneRecord.name)).all()
            return [self._zone_config(zone, floor, building) for zone, floor, building in rows]

    def zones_for_user(self, user: User) -> list[ZoneConfiguration]:
        if user.role == Role.ADMIN:
            return self.list_zones()
        buildings = self.list_buildings(user)
        result: list[ZoneConfiguration] = []
        for building in buildings:
            result.extend(self.list_zones(building_id=str(building.building_id)))
        return result

    def resolve_zone(self, identifier: str, *, include_archived: bool = False) -> ZoneConfiguration | None:
        with self.sessions() as session:
            query = (select(ZoneRecord, FloorRecord, BuildingRecord)
                     .join(FloorRecord, ZoneRecord.floor_id == FloorRecord.floor_id)
                     .join(BuildingRecord, FloorRecord.building_id == BuildingRecord.building_id))
            zone_uuid = self._uuid(identifier)
            if zone_uuid:
                rows = session.execute(query.where(ZoneRecord.zone_id == zone_uuid)).all()
            if not zone_uuid or not rows:
                rows = session.execute(query.where(or_(ZoneRecord.legacy_zone_id == identifier,
                                                       ZoneRecord.zone_key == identifier))).all()
            if not include_archived:
                rows = [(zone, floor, building) for zone, floor, building in rows
                        if zone.archived_at is None and floor.archived_at is None and building.archived_at is None]
            if len(rows) > 1:
                raise ValueError("Zone identifier is ambiguous; use the zone UUID or a building-scoped zone route.")
            if not rows:
                return None
            return self._zone_config(*rows[0])

    @staticmethod
    def _zone_config(zone: ZoneRecord, floor: FloorRecord, building: BuildingRecord) -> ZoneConfiguration:
        return ZoneConfiguration(zone_id=zone.legacy_zone_id or str(zone.zone_id),
            database_zone_id=str(zone.zone_id), zone_key=zone.zone_key,
            building_id=str(building.building_id), building_key=building.building_key,
            floor_id=str(floor.floor_id), name=zone.name, type=zone.zone_type,
            capacity=zone.capacity, area_m2=zone.area_m2,
            comfort={"min_temperature": zone.comfort_min_c, "max_temperature": zone.comfort_max_c},
            archived_at=zone.archived_at)

    def get_zone(self, identifier: str, *, include_archived: bool = False) -> dict | None:
        config = self.resolve_zone(identifier, include_archived=include_archived)
        return config.api_dict() if config else None

    def get_floor_for_zone(self, identifier: str) -> dict | None:
        config = self.resolve_zone(identifier)
        return self.get_floor(config.floor_id) if config else None

    def create_zone(self, floor_id: str, values: dict) -> dict:
        floor_uuid = self._uuid(floor_id)
        if floor_uuid is None:
            raise ValueError("Floor not found")
        row = ZoneRecord(zone_id=uuid4(), floor_id=floor_uuid, **values)
        with self.sessions.begin() as session:
            floor = session.get(FloorRecord, floor_uuid)
            if floor is None or floor.archived_at is not None:
                raise ValueError("Floor not found")
            building = session.get(BuildingRecord, floor.building_id)
            if building is None or building.archived_at is not None:
                raise ValueError("Building not found")
            session.add(row)
            session.flush()
            return self._zone_config(row, floor, building).api_dict()

    def update_zone(self, identifier: str, values: dict) -> dict | None:
        config = self.resolve_zone(identifier)
        if config is None:
            return None
        with self.sessions.begin() as session:
            row = session.get(ZoneRecord, UUID(config.database_zone_id))
            if row is None or row.archived_at is not None:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = utc_now()
            session.flush()
            floor = session.get(FloorRecord, row.floor_id)
            building = session.get(BuildingRecord, floor.building_id)
            return self._zone_config(row, floor, building).api_dict()

    def archive_zone(self, identifier: str) -> dict | None:
        config = self.resolve_zone(identifier, include_archived=True)
        if config is None:
            return None
        with self.sessions.begin() as session:
            row = session.get(ZoneRecord, UUID(config.database_zone_id))
            if row.archived_at is None:
                row.archived_at = utc_now()
                row.updated_at = row.archived_at
            floor = session.get(FloorRecord, row.floor_id)
            building = session.get(BuildingRecord, floor.building_id)
            return self._zone_config(row, floor, building).api_dict()

    def runtime_zone_configs(self) -> list[ZoneConfiguration]:
        return self.list_zones()

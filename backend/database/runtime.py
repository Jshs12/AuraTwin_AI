"""Explicit migration and deterministic legacy demo configuration bootstrap."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.core.paths import PROJECT_ROOT, resolve_project_path
from backend.database.config import DatabaseSettings
from backend.database.engine import create_database_engine, create_session_factory
from backend.database.models import (BuildingRecord, FloorRecord, OrganizationRecord,
    OrganizationMembershipRecord, UserRecord, ZoneRecord)


def upgrade_schema(engine) -> None:
    """Apply reviewed migrations using the supplied connection; never embeds URL in logs."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def create_runtime_database(settings: DatabaseSettings | None = None):
    config = settings or DatabaseSettings.from_environment()
    engine = create_database_engine(config)
    # Phase 11.2 automatically migrates only the local SQLite development
    # database. A configured PostgreSQL database must be migrated explicitly.
    if config.require_url().startswith("sqlite"):
        upgrade_schema(engine)
    return engine, create_session_factory(engine)


def bootstrap_legacy_demo_configuration(sessions: sessionmaker[Session],
                                        zones_path: str | Path | None = None) -> dict[str, int]:
    """Idempotently import the checked-in legacy demo JSON without overwriting edits."""
    path = Path(zones_path) if zones_path else resolve_project_path("data/building/zones.json")
    if not path.exists():
        return {"organizations": 0, "buildings": 0, "floors": 0, "zones": 0}
    legacy_zones = json.loads(path.read_text(encoding="utf-8"))
    organization_id = uuid5(NAMESPACE_URL, "auratwin:legacy-demo:organization")
    building_id = uuid5(NAMESPACE_URL, "auratwin:legacy-demo:building")
    floor_id = uuid5(NAMESPACE_URL, "auratwin:legacy-demo:floor:ground")
    counts = {"organizations": 0, "buildings": 0, "floors": 0, "zones": 0}

    with sessions.begin() as session:
        organization = session.get(OrganizationRecord, organization_id)
        if organization is None:
            organization = OrganizationRecord(organization_id=organization_id,
                name="AuraTwin Development Organization", slug="auratwin-development")
            session.add(organization)
            counts["organizations"] = 1
            session.flush()

        building = session.get(BuildingRecord, building_id)
        if building is None:
            building = session.scalar(select(BuildingRecord).where(
                BuildingRecord.organization_id == organization_id,
                BuildingRecord.slug == "development-building"))
        if building is None:
            building = BuildingRecord(building_id=building_id, organization_id=organization_id,
                name="AuraTwin Development Building", slug="development-building",
                building_key="development-building", timezone="UTC", address={})
            session.add(building)
            counts["buildings"] = 1
            session.flush()

        # Existing bootstrap identities can be re-run without duplicate membership.
        for user in session.scalars(select(UserRecord)).all():
            if user.role == "ADMIN":
                membership_exists = session.scalar(select(OrganizationMembershipRecord.membership_id)
                    .where(OrganizationMembershipRecord.organization_id == organization_id,
                           OrganizationMembershipRecord.user_id == user.user_id))
                if membership_exists is None:
                    session.add(OrganizationMembershipRecord(organization_id=organization_id,
                                                               user_id=user.user_id))

        floor = session.get(FloorRecord, floor_id)
        if floor is None:
            floor = session.scalar(select(FloorRecord).where(
                FloorRecord.building_id == building.building_id, FloorRecord.floor_key == "ground"))
        if floor is None:
            floor = FloorRecord(floor_id=floor_id, building_id=building.building_id,
                name="Ground Floor", floor_key="ground", level_number=0)
            session.add(floor)
            counts["floors"] = 1
            session.flush()

        for entry in legacy_zones:
            legacy_zone_id = entry["zone_id"]
            zone_id = uuid5(NAMESPACE_URL, f"auratwin:legacy-demo:zone:{legacy_zone_id}")
            if (session.get(ZoneRecord, zone_id) is not None or session.scalar(select(ZoneRecord.zone_id)
                    .where(ZoneRecord.legacy_zone_id == legacy_zone_id)) is not None):
                continue
            comfort = entry["comfort"]
            session.add(ZoneRecord(zone_id=zone_id, floor_id=floor.floor_id,
                zone_key=legacy_zone_id, legacy_zone_id=legacy_zone_id,
                name=entry["name"], zone_type=entry["type"], capacity=entry["capacity"],
                area_m2=entry["area_m2"], comfort_min_c=comfort["min_temperature"],
                comfort_max_c=comfort["max_temperature"]))
            counts["zones"] += 1
    return counts


def initialize_local_demo_database(settings: DatabaseSettings | None = None):
    """Convenience for the local app: migrate and bootstrap only SQLite stores."""
    config = settings or DatabaseSettings.from_environment()
    if config.url is None:
        config = DatabaseSettings(url=f"sqlite+pysqlite:///{resolve_project_path(Path('data') / 'auratwin.db').as_posix()}")
    engine, sessions = create_runtime_database(config)
    if config.require_url().startswith("sqlite"):
        bootstrap_legacy_demo_configuration(sessions)
    return engine, sessions

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlalchemy.exc import IntegrityError

from backend.database.config import DatabaseSettings
from backend.database.engine import create_database_engine, create_session_factory
from backend.database.models import (Base, BuildingRecord, DeviceRecord, FloorRecord,
    IntegrationRecord, OrganizationMembershipRecord, OrganizationRecord, PointMappingRecord, UserBuildingAccessRecord,
    UserRecord, ZoneRecord)
from backend.database.repositories import (BuildingData, OrganizationData,
    SQLAlchemyBuildingRepository, SQLAlchemyOrganizationRepository, SQLAlchemyUserRepository)
from backend.security.models import User
from backend.security.repository import BuildingAccessRepository
from backend.security.roles import Role


@pytest.fixture
def database():
    engine = create_database_engine(url="sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine, create_session_factory(engine)
    engine.dispose()


def test_database_settings_are_optional_and_secret_is_not_in_repr(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert DatabaseSettings.from_environment().url is None
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_database_engine(DatabaseSettings())

    settings = DatabaseSettings(url="postgresql+psycopg://user:private@host/db")
    assert "private" not in repr(settings)
    assert settings.require_url().endswith("@host/db")


def test_models_and_repository_relationships(database):
    _engine, sessions = database
    organizations = SQLAlchemyOrganizationRepository(sessions)
    buildings = SQLAlchemyBuildingRepository(sessions)
    organization_id, building_id = str(uuid4()), str(uuid4())
    organizations.add(OrganizationData(organization_id, "North Campus", "north-campus"))
    buildings.add(BuildingData(building_id, organization_id, "Library", "library", "Asia/Kolkata", {"city": "Pune"}))

    assert organizations.get(organization_id).slug == "north-campus"
    assert buildings.get(building_id).organization_id == organization_id
    assert [row.name for row in buildings.list_for_organization(organization_id)] == ["Library"]

    with sessions() as session:
        building = session.get(BuildingRecord, UUID(building_id))
        floor = FloorRecord(building=building, name="Ground", floor_key="ground", level_number=0)
        zone = ZoneRecord(floor=floor, zone_key="classroom_01", name="Classroom 01",
            zone_type="classroom", capacity=30, area_m2=45, comfort_min_c=20, comfort_max_c=26)
        session.add(floor)
        session.flush()
        assert zone.floor.floor_id == floor.floor_id
        membership = OrganizationMembershipRecord(organization=building.organization,
            user=UserRecord(email="organization-user@example.test", email_normalized="organization-user@example.test",
                password_hash="hash", role="ADMIN"))
        session.add(membership)
        session.flush()
        assert membership.organization.name == "North Campus"
        assert membership.user.role == "ADMIN"


def test_role_constraint_and_postgresql_ddl_are_supported(database):
    engine, sessions = database
    with pytest.raises(IntegrityError):
        with sessions.begin() as session:
            session.add(UserRecord(email="wrong-role@example.test", email_normalized="wrong-role@example.test",
                password_hash="hash", role="OWNER"))

    statements = [str(CreateTable(table).compile(dialect=postgresql.dialect()))
                  for table in Base.metadata.sorted_tables]
    assert all("CREATE TABLE" in statement for statement in statements)
    assert any("ck_users_role" in statement for statement in statements)


def test_user_access_adapter_preserves_exact_roles_and_building_scope(database):
    _engine, sessions = database
    organization_id, building_id, operator_id, admin_id = (str(uuid4()) for _ in range(4))
    organizations = SQLAlchemyOrganizationRepository(sessions)
    buildings = SQLAlchemyBuildingRepository(sessions)
    organizations.add(OrganizationData(organization_id, "Org", "org"))
    buildings.add(BuildingData(building_id, organization_id, "Building", "building", "UTC", {}))
    users = SQLAlchemyUserRepository(sessions)
    users.add(User(operator_id, "OP@example.test", "argon-hash", Role.OPERATOR, True, frozenset({building_id})))
    users.add(User(admin_id, "admin@example.test", "argon-hash", Role.ADMIN, True, frozenset()))

    operator = users.get_by_email("op@EXAMPLE.test")
    admin = users.get_by_id(admin_id)
    access = BuildingAccessRepository()
    assert operator.role is Role.OPERATOR
    assert operator.building_ids == frozenset({building_id})
    assert access.has_access(operator, building_id)
    assert not access.has_access(operator, str(uuid4()))
    assert admin.role is Role.ADMIN and access.has_access(admin, building_id)
    assert {row.role for row in users.list_users()} == {Role.ADMIN, Role.OPERATOR}
    with pytest.raises(IntegrityError):
        users.add(User(str(uuid4()), "OP@example.test", "argon-hash", Role.OPERATOR, True, frozenset({building_id})))
    assert users.deactivate(operator_id).active is False
    assert users.get_by_id(operator_id).active is False


def test_normalized_configuration_and_device_point_mapping_constraints(database):
    engine, _sessions = database
    organization_id, building_id, floor_id, zone_id, integration_id, device_id = [uuid4() for _ in range(6)]
    with create_session_factory(engine).begin() as session:
        org = OrganizationRecord(organization_id=organization_id, name="Org", slug="org")
        building = BuildingRecord(building_id=building_id, organization=org, name="B", slug="b", address={})
        floor = FloorRecord(floor_id=floor_id, building=building, name="G", floor_key="g")
        zone = ZoneRecord(zone_id=zone_id, floor=floor, zone_key="z1", name="Zone 1",
            zone_type="classroom", capacity=10, area_m2=20, comfort_min_c=19, comfort_max_c=27)
        integration = IntegrationRecord(integration_id=integration_id, building=building,
            name="HVAC", integration_type="bacnet_ip", configuration={})
        device = DeviceRecord(device_id=device_id, integration=integration, zone=zone,
            external_device_id="device-1", name="Controller", device_type="hvac")
        point = PointMappingRecord(device=device, external_point_id="analog:1",
            logical_signal="cooling_setpoint", data_type="number", unit="degC", writable=True)
        session.add_all([org, building, floor, zone, integration, device, point])

    with create_session_factory(engine).begin() as session:
        access_user = UserRecord(email="op@example.test", email_normalized="op@example.test",
            password_hash="hash", role="OPERATOR")
        session.add(access_user)
        session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(UserBuildingAccessRecord(user_id=access_user.user_id, building_id=building_id))
                session.add(UserBuildingAccessRecord(user_id=access_user.user_id, building_id=building_id))
                session.flush()


def test_initial_alembic_migration_up_and_down_on_local_sqlite(tmp_path, monkeypatch):
    database_path = tmp_path / "migration.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))

    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"organizations", "users", "organization_memberships", "user_building_access", "buildings", "floors",
                "zones", "integrations", "devices", "point_mappings", "alembic_version"} <= tables
        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()

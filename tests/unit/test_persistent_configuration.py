from uuid import uuid4
import json
import contextlib
import io
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from fastapi.testclient import TestClient

from backend.database.engine import create_database_engine, create_session_factory
from backend.database.models import Base, BuildingRecord, FloorRecord, OrganizationRecord, ZoneRecord
from backend.database.runtime import bootstrap_legacy_demo_configuration
from backend.database.configuration import SQLAlchemyConfigurationRepository
from backend.database.repositories import SQLAlchemyOrganizationRepository, OrganizationData
from backend.api.main import app
from backend.security.roles import Role
from tests.security_test_utils import make_client


def _repository(tmp_path):
    engine = create_database_engine(url=f"sqlite+pysqlite:///{tmp_path / 'configuration.db'}")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    return engine, sessions, SQLAlchemyConfigurationRepository(sessions)


def test_persistent_configuration_relationships_crud_and_lifecycle(tmp_path):
    engine, sessions, repository = _repository(tmp_path)
    organization_id = str(uuid4())
    SQLAlchemyOrganizationRepository(sessions).add(
        OrganizationData(organization_id, "Configuration Test", f"config-{uuid4().hex[:10]}"))
    building = repository.create_building(organization_id, {
        "name": "North Building", "slug": "north", "building_key": f"{organization_id}:north",
        "timezone": "UTC", "address": {},
    })
    floor = repository.create_floor(building["building_id"], {
        "name": "Level 1", "floor_key": "level-1", "level_number": 1,
    })
    zone = repository.create_zone(floor["floor_id"], {
        "zone_key": "room-a", "name": "Room A", "zone_type": "classroom", "capacity": 24,
        "area_m2": 35.5, "comfort_min_c": 20.0, "comfort_max_c": 25.0,
    })
    assert repository.get_building(building["building_id"])["organization_id"] == organization_id
    assert repository.get_floor(floor["floor_id"])["building_id"] == building["building_id"]
    assert repository.resolve_zone(zone["zone_id"]).floor_id == floor["floor_id"]
    assert repository.update_building(building["building_id"], {"name": "North Annex"})["name"] == "North Annex"
    assert repository.update_floor(floor["floor_id"], {"name": "First Floor"})["name"] == "First Floor"
    assert repository.update_zone(zone["zone_id"], {"capacity": 30})["capacity"] == 30
    assert repository.archive_zone(zone["zone_id"])["archived_at"] is not None
    assert repository.resolve_zone(zone["zone_id"]) is None
    assert repository.archive_floor(floor["floor_id"])["archived_at"] is not None
    assert repository.archive_building(building["building_id"])["archived_at"] is not None
    engine.dispose()


def test_legacy_demo_bootstrap_is_idempotent_and_preserves_all_zone_ids(tmp_path):
    engine, sessions, _repository_instance = _repository(tmp_path)
    first = bootstrap_legacy_demo_configuration(sessions)
    second = bootstrap_legacy_demo_configuration(sessions)
    assert first == {"organizations": 1, "buildings": 1, "floors": 1, "zones": 10}
    assert second == {"organizations": 0, "buildings": 0, "floors": 0, "zones": 0}
    with sessions() as session:
        assert session.query(BuildingRecord).count() == 1
        assert session.query(FloorRecord).count() == 1
        assert session.query(ZoneRecord).count() == 10
        assert {row.legacy_zone_id for row in session.query(ZoneRecord)} == {
            "classroom_01", "classroom_02", "lab_01", "lab_02", "office_01", "office_02",
            "meeting_room_01", "server_room_01", "corridor_01", "auditorium_01",
        }
        imported = {row.legacy_zone_id: row for row in session.query(ZoneRecord)}
        source = json.loads((Path(__file__).resolve().parents[2] / "data/building/zones.json").read_text())
        for item in source:
            row = imported[item["zone_id"]]
            assert row.capacity == item["capacity"]
            assert row.area_m2 == item["area_m2"]
            assert row.comfort_min_c == item["comfort"]["min_temperature"]
            assert row.comfort_max_c == item["comfort"]["max_temperature"]
    engine.dispose()


def test_operator_building_scope_and_admin_oversight_are_repository_backed():
    repository = app.state.configuration_repository
    organization_id = str(uuid4())
    app.state.organization_repository.add(OrganizationData(
        organization_id, "Isolated Organization", f"isolated-{uuid4().hex[:10]}"))
    building = repository.create_building(organization_id, {
        "name": "Restricted Building", "slug": f"restricted-{uuid4().hex[:8]}",
        "building_key": f"{organization_id}:restricted", "timezone": "UTC", "address": {},
    })
    floor = repository.create_floor(building["building_id"], {
        "name": "Ground", "floor_key": "ground", "level_number": 0,
    })
    zone = repository.create_zone(floor["floor_id"], {
        "zone_key": "private-room", "name": "Private Room", "zone_type": "office", "capacity": 3,
        "area_m2": 10.0, "comfort_min_c": 20.0, "comfort_max_c": 25.0,
    })

    operator = make_client(role=Role.OPERATOR, building_ids={"development-building"})
    development = repository.resolve_building("development-building")
    visible_organizations = {item["organization_id"]
                             for item in operator.get("/api/organizations").json()["organizations"]}
    assert str(development.organization_id) in visible_organizations
    assert organization_id not in visible_organizations
    assert operator.get(f"/api/buildings/{building['building_id']}").status_code == 403
    assert operator.get(f"/api/zones/{zone['zone_id']}").status_code == 403
    assert all(item["building_id"] != building["building_id"]
               for item in operator.get("/api/buildings").json()["buildings"])

    admin = make_client(role=Role.ADMIN, building_ids=set())
    assert admin.get(f"/api/buildings/{building['building_id']}").status_code == 200
    assert admin.post(f"/api/buildings/{building['building_id']}/floors", json={
        "name": "Denied", "floor_key": "denied", "level_number": 2,
    }).status_code == 403
    assert TestClient(app).get("/api/buildings").status_code == 401


def test_operator_configuration_api_is_scoped_and_archives_without_deleting():
    client = make_client(role=Role.OPERATOR, building_ids={"development-building"})
    development = app.state.configuration_repository.resolve_building("development-building")
    organization_id = str(development.organization_id)
    created_building = client.post("/api/buildings", json={
        "organization_id": organization_id, "name": "Operator Site",
        "slug": f"operator-site-{uuid4().hex[:8]}", "timezone": "UTC", "address": {},
    })
    assert created_building.status_code == 201
    building_id = created_building.json()["building_id"]
    floor_response = client.post(f"/api/buildings/{building_id}/floors", json={
        "name": "Level 1", "floor_key": "level-1", "level_number": 1,
    })
    assert floor_response.status_code == 201
    floor_id = floor_response.json()["floor_id"]
    zone_response = client.post(f"/api/floors/{floor_id}/zones", json={
        "zone_key": "room-one", "name": "Room One", "type": "classroom", "capacity": 20,
        "area_m2": 30.0, "comfort": {"min_temperature": 20.0, "max_temperature": 25.0},
    })
    assert zone_response.status_code == 201
    zone_id = zone_response.json()["zone_id"]
    assert client.get(f"/api/buildings/{building_id}/zones").json()["zones"][0]["zone_id"] == zone_id
    update = client.patch(f"/api/zones/{zone_id}/configuration", json={"capacity": 24})
    assert update.status_code == 200 and update.json()["capacity"] == 24
    archived = client.delete(f"/api/zones/{zone_id}/configuration")
    assert archived.status_code == 200 and archived.json()["archived_at"]
    assert client.get(f"/api/zones/{zone_id}").status_code == 404
    assert client.get(f"/api/buildings/{building_id}/zones").json()["zones"] == []
    assert client.delete(f"/api/buildings/{building_id}").status_code == 200


def test_phase_11_2_migration_backfills_legacy_identifiers(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "20261001_01")
    organization_id, development_id, second_id = (uuid4() for _ in range(3))
    floor_one, floor_two, zone_one, zone_two, zone_three = (uuid4() for _ in range(5))
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO organizations (organization_id, name, slug) VALUES (:id, 'Legacy', 'legacy')"),
                           {"id": organization_id.hex})
        for building_id, slug in ((development_id, "development-building"), (second_id, "second")):
            connection.execute(text("INSERT INTO buildings (building_id, organization_id, name, slug, timezone, address) VALUES (:id, :org, :name, :slug, 'UTC', '{}')"),
                {"id": building_id.hex, "org": organization_id.hex, "name": slug, "slug": slug})
        for floor_id, building_id, key in ((floor_one, development_id, "one"), (floor_two, second_id, "two")):
            connection.execute(text("INSERT INTO floors (floor_id, building_id, name, floor_key, level_number) VALUES (:id, :building, :name, :key, 0)"),
                {"id": floor_id.hex, "building": building_id.hex, "name": key, "key": key})
        for zone_id, floor_id, key in ((zone_one, floor_one, "shared-room"),
                                       (zone_two, floor_two, "shared-room"),
                                       (zone_three, floor_two, "only-room")):
            connection.execute(text("INSERT INTO zones (zone_id, floor_id, zone_key, name, zone_type, capacity, area_m2, comfort_min_c, comfort_max_c) VALUES (:id, :floor, :key, :key, 'classroom', 10, 20, 20, 25)"),
                {"id": zone_id.hex, "floor": floor_id.hex, "key": key})
    command.upgrade(config, "head")
    with engine.connect() as connection:
        building_keys = dict(connection.execute(text("SELECT slug, building_key FROM buildings")).all())
        legacy_ids = [row[0] for row in connection.execute(text("SELECT legacy_zone_id FROM zones"))]
    assert building_keys["development-building"] == "development-building"
    assert building_keys["second"] == f"{organization_id.hex}:second"
    assert {value for value in legacy_ids if value is not None} == {"only-room"}
    assert legacy_ids.count(None) == 2
    engine.dispose()


def test_phase_11_2_migration_generates_postgresql_offline_sql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "building_key" in sql and "legacy_zone_id" in sql
    assert "UPDATE buildings" in sql and "UPDATE zones" in sql

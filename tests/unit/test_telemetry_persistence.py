from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from backend.database.configuration import SQLAlchemyConfigurationRepository
from backend.database.engine import create_database_engine, create_session_factory
from backend.database.models import Base, OrganizationRecord, BuildingRecord, FloorRecord, ZoneRecord
from backend.database.repositories import OrganizationData
from backend.energy.telemetry import BuildingEnergyTelemetry
from backend.schemas.control import BuildingControlState
from backend.schemas.data_quality import QualityState
from backend.schemas.energy import EnergyReading, Tariff
from backend.schemas.events import OccupancyEvent
from backend.schemas.state import ZoneState
from backend.schemas.zone import Zone
from backend.security.roles import Role
from backend.telemetry.repository import SQLAlchemyTelemetryRepository
from backend.telemetry.service import TelemetryPersistenceService, TelemetryRetentionPolicy
from tests.security_test_utils import make_client


@pytest.fixture
def telemetry_context(tmp_path):
    engine = create_database_engine(url=f"sqlite+pysqlite:///{tmp_path / 'telemetry.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    config = SQLAlchemyConfigurationRepository(sessions)
    org = OrganizationRecord(organization_id=uuid4(), name="Telemetry Org", slug=f"org-{uuid4().hex}")
    with sessions.begin() as session:
        session.add(org)
        session.flush()
        building = BuildingRecord(organization_id=org.organization_id, name="Building",
            slug="building", building_key=f"building-{uuid4().hex}", timezone="UTC", address={})
        session.add(building)
        session.flush()
        floor = FloorRecord(building_id=building.building_id, name="Ground", floor_key="ground")
        session.add(floor)
        session.flush()
        zone = ZoneRecord(floor_id=floor.floor_id, zone_key="room", legacy_zone_id="room",
            name="Room", zone_type="classroom", capacity=20, area_m2=30,
            comfort_min_c=20, comfort_max_c=26)
        session.add(zone)
        session.flush()
        ids = (str(org.organization_id), str(building.building_id),
               str(floor.floor_id), str(zone.zone_id))
    repository = SQLAlchemyTelemetryRepository(sessions)
    service = TelemetryPersistenceService(repository, config)
    yield engine, sessions, config, repository, service, ids
    engine.dispose()


def _zone_state(zone_id="room", observed_at=None):
    observed_at = observed_at or datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    tariff = Tariff(tariff_id="demo", rate_per_kwh=0.2, currency="USD",
        observed_at=observed_at, source="simulated_tariff", simulated=True)
    occupancy = OccupancyEvent(zone_id=zone_id, people_count=5, capacity=20,
        occupancy_percentage=25, occupancy_state="LOW", timestamp=observed_at,
        observed_at=observed_at, source="simulated_camera_metadata", simulated=True)
    energy = EnergyReading(zone_id=zone_id, power_kw=1.25, energy_kwh=4.5, cost=0.9,
        tariff=tariff, timestamp=observed_at, observed_at=observed_at,
        source="simulated_meter", is_simulated=True)
    state = ZoneState(zone=Zone(zone_id=zone_id, name="Room", type="classroom", capacity=20,
        area_m2=30, comfort={"min_temperature": 20, "max_temperature": 26}),
        occupancy=occupancy, temperature=23.5, energy=energy, tariff=tariff,
        hvac_status=BuildingControlState(zone_id=zone_id, object_id="setpoint", present_value=22,
            provider="simulated_hvac", simulated=True, observed_at=observed_at,
            setpoint_observed_at=observed_at, current_temperature=23.5),
        occupancy_source="simulated_camera_metadata", temperature_source="simulated_hvac",
        temperature_observed_at=observed_at, temperature_simulated=True)
    # Quality is copied from an existing assessment rather than invented by persistence.
    from backend.services.data_quality import DataQualityGate
    state.data_quality = DataQualityGate(max_age_seconds={}, ranges={}).assess_zone_state(
        state, now=observed_at)
    return state


def test_zone_state_persists_only_tenant_scoped_scalar_observations(telemetry_context):
    _, _, config, repository, service, ids = telemetry_context
    state = _zone_state()
    assert service.persist_zone_state(state) == 6
    rows = repository.list_zone(organization_id=ids[0], building_id=ids[1], zone_id=ids[3])
    assert {row.signal.value for row in rows} == {
        "occupancy", "temperature", "power", "energy", "cost", "tariff_rate"}
    occupancy = next(row for row in rows if row.signal.value == "occupancy")
    assert (occupancy.value, occupancy.unit) == (5, "people")
    assert occupancy.source == "simulated_camera_metadata"
    assert occupancy.simulated is True and occupancy.quality_state == QualityState.VALID.value
    assert occupancy.organization_id == ids[0] and occupancy.building_id == ids[1]
    assert occupancy.floor_id == ids[2] and occupancy.zone_id == ids[3]
    assert occupancy.observed_at.replace(tzinfo=timezone.utc) == state.occupancy.observed_at
    assert occupancy.ingested_at is not None
    assert not hasattr(occupancy, "image") and not hasattr(occupancy, "frame")


def test_missing_observation_time_is_not_replaced_by_read_or_ingestion_time(telemetry_context):
    _, _, _, repository, service, ids = telemetry_context
    state = _zone_state()
    state.occupancy.observed_at = None
    state.temperature_observed_at = None
    state.energy.observed_at = None
    state.tariff.observed_at = None
    assert service.persist_zone_state(state) == 0
    assert repository.list_zone(organization_id=ids[0], building_id=ids[1], zone_id=ids[3]) == []


def test_idempotency_is_based_on_scope_signal_observation_time_and_source(telemetry_context):
    _, _, _, repository, service, ids = telemetry_context
    state = _zone_state()
    assert service.persist_zone_state(state) == 6
    assert service.persist_zone_state(state) == 0
    rows = repository.list_zone(organization_id=ids[0], building_id=ids[1], zone_id=ids[3])
    assert len(rows) == 6
    later = _zone_state(observed_at=state.occupancy.observed_at + timedelta(seconds=1))
    assert service.persist_zone_state(later) == 6
    assert len(repository.list_zone(organization_id=ids[0], building_id=ids[1], zone_id=ids[3])) == 12


def test_queries_enforce_organization_building_zone_and_time_scopes(telemetry_context):
    _, _, _, repository, service, ids = telemetry_context
    earlier = _zone_state(observed_at=datetime(2026, 9, 30, 11, tzinfo=timezone.utc))
    later = _zone_state(observed_at=datetime(2026, 9, 30, 12, tzinfo=timezone.utc))
    service.persist_zone_state(earlier)
    service.persist_zone_state(later)
    rows = repository.list_building(organization_id=ids[0], building_id=ids[1],
        start_at=datetime(2026, 9, 30, 11, 30, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 30, 13, tzinfo=timezone.utc))
    assert len(rows) == 6
    assert repository.list_building(organization_id=str(uuid4()), building_id=ids[1]) == []
    assert repository.list_building(organization_id=ids[0], building_id=str(uuid4())) == []
    assert repository.list_zone(organization_id=ids[0], building_id=ids[1], zone_id=str(uuid4())) == []


def test_floor_signal_and_time_queries_and_domain_aggregations(telemetry_context):
    _, _, _, repository, service, ids = telemetry_context
    base = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    first = _zone_state(observed_at=base)
    second = _zone_state(observed_at=base + timedelta(hours=1))
    second.occupancy.people_count = 9
    second.occupancy.occupancy_percentage = 45
    second.occupancy.occupancy_state = "MEDIUM"
    second.temperature = 25.5
    second.energy.power_kw = 2.25
    second.energy.energy_kwh = 8.5
    service.persist_zone_state(first)
    service.persist_zone_state(second)
    occupancy = repository.list_floor(organization_id=ids[0], building_id=ids[1],
        floor_id=ids[2], zone_id=ids[3], signal="occupancy",
        start_at=base, end_at=base + timedelta(hours=2))
    assert [item.value for item in occupancy] == [9, 5]
    aggregates = {item["signal"]: item for item in repository.aggregate(
        organization_id=ids[0], building_id=ids[1], start_at=base,
        end_at=base + timedelta(hours=2), floor_id=ids[2])}
    assert aggregates["temperature"]["minimum"] == 23.5
    assert aggregates["temperature"]["maximum"] == 25.5
    assert aggregates["temperature"]["average"] == 24.5
    assert aggregates["occupancy"]["average"] == 7
    assert aggregates["power"]["average"] == 1.75
    assert aggregates["energy"]["average"] == 6.5
    assert "sum" not in aggregates["energy"]


def test_history_time_ranges_signal_floor_and_empty_result_api():
    from backend.api.main import app
    zone = app.state.configuration_repository.resolve_zone("classroom_01")
    state = app.state.zone_state_service.get_zone_state("classroom_01")
    client = make_client(role=Role.ADMIN)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    params = {"start_time": start.isoformat(), "end_time": now.isoformat(),
              "floor_id": zone.floor_id, "zone_id": zone.zone_id, "signal": "power"}
    response = client.get(f"/api/buildings/{zone.building_id}/telemetry", params=params)
    assert response.status_code == 200
    body = response.json()
    assert all(point["signal"] == "power" for point in body["observations"])
    assert body["aggregations"][0]["signal"] == "power"
    assert {"organization_id", "building_id", "zone_id", "value", "unit",
            "observed_at", "ingested_at", "source", "quality_state", "simulated"} <= set(body["observations"][0])
    floor_response = client.get(f"/api/floors/{zone.floor_id}/telemetry", params={
        "start_time": start.isoformat(), "end_time": now.isoformat(), "signal": "temperature"})
    assert floor_response.status_code == 200
    assert all(point["signal"] == "temperature" for point in floor_response.json()["observations"])
    empty = client.get(f"/api/zones/{zone.zone_id}/telemetry", params={
        "start_time": (now - timedelta(days=90)).isoformat(),
        "end_time": (now - timedelta(days=60)).isoformat(), "signal": "power"})
    assert empty.status_code == 200 and empty.json()["observations"] == []
    assert client.get(f"/api/zones/{zone.zone_id}/telemetry", params={
        "start_time": now.isoformat(), "end_time": now.isoformat()}).status_code == 422
    assert client.get(f"/api/zones/{zone.zone_id}/telemetry", params={
        "start_time": "2026-10-01T00:00:00", "end_time": now.isoformat()}).status_code == 422
    assert client.get(f"/api/zones/{zone.zone_id}/telemetry", params={
        "start_time": start.isoformat(), "end_time": (now + timedelta(minutes=1)).isoformat()}).status_code == 422
    assert client.get(f"/api/zones/{zone.zone_id}/telemetry", params={
        "start_time": start.isoformat(), "end_time": now.isoformat(), "signal": "secret"}).status_code == 422
    assert client.get(f"/api/buildings/{zone.building_id}/telemetry", params={
        "start_time": start.isoformat(), "end_time": now.isoformat(),
        "floor_id": str(uuid4())}).status_code == 404
    assert client.get(f"/api/buildings/{zone.building_id}/telemetry", params={
        "start_time": start.isoformat(), "end_time": now.isoformat(),
        "zone_id": str(uuid4())}).status_code == 404


def test_query_limit_configuration_is_positive_and_documentable():
    from backend.telemetry.service import telemetry_query_max_limit
    assert telemetry_query_max_limit({}) == 500
    assert telemetry_query_max_limit({"TELEMETRY_QUERY_MAX_LIMIT": "75"}) == 75
    with pytest.raises(ValueError):
        telemetry_query_max_limit({"TELEMETRY_QUERY_MAX_LIMIT": "0"})


def test_database_rejects_cross_tenant_zone_lineage(telemetry_context):
    _, _, _, repository, _, ids = telemetry_context
    from backend.schemas.telemetry import TelemetryObservation
    observation = TelemetryObservation(organization_id=str(uuid4()), building_id=ids[1],
        floor_id=ids[2], zone_id=ids[3], signal="occupancy", value=2, unit="people",
        observed_at=datetime.now(timezone.utc), source="test", quality_state="VALID", simulated=True)
    with pytest.raises(IntegrityError):
        repository.add_many([observation])


def test_retention_configuration_is_explicit_and_cleanup_is_opt_in(telemetry_context):
    _, _, _, repository, service, ids = telemetry_context
    state = _zone_state()
    service.persist_zone_state(state)
    assert TelemetryRetentionPolicy.from_environment({}).days is None
    assert TelemetryRetentionPolicy.from_environment({"TELEMETRY_RETENTION_DAYS": "30"}).days == 30
    with pytest.raises(ValueError):
        TelemetryRetentionPolicy.from_environment({"TELEMETRY_RETENTION_DAYS": "0"})
    no_retention = TelemetryPersistenceService(repository, telemetry_context[2], TelemetryRetentionPolicy())
    assert no_retention.cleanup_expired(now=datetime.now(timezone.utc)) == 0
    retained = TelemetryPersistenceService(repository, telemetry_context[2], TelemetryRetentionPolicy(days=30))
    assert retained.cleanup_expired(now=datetime(2026, 11, 1, tzinfo=timezone.utc)) == 6
    assert repository.list_zone(organization_id=ids[0], building_id=ids[1], zone_id=ids[3]) == []


def test_existing_zone_and_building_telemetry_apis_are_authorized_and_read_only():
    from backend.api.main import app
    zone = app.state.configuration_repository.resolve_zone("classroom_01")
    app.state.zone_state_service.get_zone_state("classroom_01")
    admin = make_client(role=Role.ADMIN)
    latest = admin.get("/api/zones/classroom_01/telemetry/latest")
    assert latest.status_code == 200
    assert latest.json()["observations"]
    assert admin.get("/api/telemetry/latest", params={"zone_id": "classroom_01", "signal": "occupancy"}).status_code == 200
    now = datetime.now(timezone.utc)
    params = {"start_time": (now - timedelta(days=1)).isoformat(), "end_time": now.isoformat()}
    building = admin.get(f"/api/buildings/{zone.building_id}/telemetry", params=params)
    assert building.status_code == 200 and building.json()["observations"]
    operator = make_client(role=Role.OPERATOR, building_ids={zone.building_key})
    assert operator.get(f"/api/zones/{zone.zone_id}/telemetry", params=params).status_code == 200
    config = app.state.configuration_repository
    organization_id = str(app.state.configuration_repository.resolve_building(
        zone.building_id).organization_id)
    other_building = config.create_building(organization_id, {
        "name": "Unassigned", "slug": f"unassigned-{uuid4().hex[:8]}",
        "building_key": f"unassigned-{uuid4().hex}", "timezone": "UTC", "address": {},
    })
    assert operator.get(f"/api/buildings/{other_building['building_id']}/telemetry", params=params).status_code == 403
    assert admin.get(f"/api/buildings/{other_building['building_id']}/telemetry", params=params).status_code == 200
    assert TestClient(app).get(f"/api/zones/{zone.zone_id}/telemetry").status_code == 401
    other_org_id = str(uuid4())
    app.state.organization_repository.add(OrganizationData(
        other_org_id, "Other Tenant", f"other-{uuid4().hex[:10]}"))
    other_tenant_building = config.create_building(other_org_id, {
        "name": "Other Tenant Building", "slug": f"tenant-{uuid4().hex[:8]}",
        "building_key": f"tenant-{uuid4().hex}", "timezone": "UTC", "address": {},
    })
    other_floor = config.create_floor(other_tenant_building["building_id"], {
        "name": "Ground", "floor_key": "ground", "level_number": 0})
    other_zone = config.create_zone(other_floor["floor_id"], {
        "zone_key": "private", "name": "Private", "zone_type": "office", "capacity": 5,
        "area_m2": 12, "comfort_min_c": 20, "comfort_max_c": 25})
    assert operator.get(f"/api/buildings/{other_tenant_building['building_id']}/telemetry",
                        params=params).status_code == 403
    mismatch = admin.get(f"/api/buildings/{zone.building_id}/telemetry",
        params={**params, "zone_id": other_zone["zone_id"]})
    assert mismatch.status_code == 404
    assert admin.get("/api/zones/classroom_01/telemetry", params={**params, "limit": 1001}).status_code == 422


def test_demo_energy_history_stays_in_memory_simulated_only():
    telemetry = BuildingEnergyTelemetry()
    sample = telemetry.record({}, elapsed_hours=0)
    assert sample["simulated"] is True
    assert len(telemetry.samples) == 1

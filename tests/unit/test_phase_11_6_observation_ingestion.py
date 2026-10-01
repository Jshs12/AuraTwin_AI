from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from backend.database.configuration import SQLAlchemyConfigurationRepository
from backend.database.engine import create_session_factory
from backend.database.models import (Base, BuildingRecord, DeviceRecord, FloorRecord,
    IntegrationRecord, OrganizationRecord, PointMappingRecord, ZoneRecord)
from backend.integrations.simulated_observations import ExplicitValueSimulatedProvider
from backend.schemas.data_quality import QualityState
from backend.schemas.provider_observation import ProviderObservation
from backend.services.data_quality import DataQualityGate
from backend.telemetry.ingestion import ProviderObservationIngestionService
from backend.telemetry.repository import SQLAlchemyTelemetryRepository
from backend.telemetry.service import TelemetryPersistenceService


@pytest.fixture
def ingestion_context(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'ingestion.db'}")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    org_id, building_id, floor_id, zone_id = [uuid4() for _ in range(4)]
    integration_id, device_id, point_id = [uuid4() for _ in range(3)]
    with sessions.begin() as session:
        session.add(OrganizationRecord(organization_id=org_id, name="Ingest Org", slug=f"org-{uuid4().hex[:8]}"))
        session.add(BuildingRecord(building_id=building_id, organization_id=org_id,
            name="Ingest Building", slug=f"building-{uuid4().hex[:8]}",
            building_key=f"building-{uuid4().hex[:8]}", address={}))
        session.add(FloorRecord(floor_id=floor_id, building_id=building_id, name="Floor", floor_key="floor"))
        session.add(ZoneRecord(zone_id=zone_id, floor_id=floor_id, zone_key="room",
            legacy_zone_id=f"room-{uuid4().hex[:8]}", name="Room", zone_type="classroom", capacity=20,
            area_m2=40, comfort_min_c=19, comfort_max_c=26))
        session.add(IntegrationRecord(integration_id=integration_id, building_id=building_id,
            name="simulated", integration_type="BACNET", status="CONFIGURED", configuration={"simulated": True}))
        session.add(DeviceRecord(device_id=device_id, integration_id=integration_id,
            external_device_id="controller-1", name="Controller", device_type="HVAC", status="CONFIGURED"))
        session.add(PointMappingRecord(point_mapping_id=point_id, device_id=device_id, zone_id=zone_id,
            external_point_id="AI:3", logical_signal="temperature", data_type="number", unit="C",
            readable=True, writable=False, metadata_json={}, mapping_status="CONFIRMED", mapping_source="OPERATOR"))
    configuration = SQLAlchemyConfigurationRepository(sessions)
    telemetry_repository = SQLAlchemyTelemetryRepository(sessions)
    telemetry = TelemetryPersistenceService(telemetry_repository, configuration)
    service = ProviderObservationIngestionService(sessions, telemetry)
    yield {"engine": engine, "sessions": sessions, "ids": (integration_id, device_id, point_id, zone_id),
           "service": service, "configuration": configuration, "telemetry": telemetry}
    engine.dispose()


def _observation(context, *, value=23.4, observed_at=None, **updates):
    integration_id, device_id, point_id, _zone_id = context["ids"]
    data = {"integration_id": str(integration_id), "device_id": str(device_id),
        "point_mapping_id": str(point_id), "observed_at": observed_at or datetime.now(timezone.utc),
        "value": value, "source": "explicit_value_simulated_provider", "simulated": True}
    data.update(updates)
    return ProviderObservation(**data)


def test_confirmed_mapping_persists_with_timestamp_source_quality_and_provenance(ingestion_context):
    context = ingestion_context
    observation = _observation(context)
    result = context["service"].ingest(observation)
    assert result.accepted and result.persisted and result.quality_state == QualityState.VALID
    scope = context["configuration"].telemetry_scope(str(context["ids"][3]))
    saved = context["telemetry"].list_zone(organization_id=scope["organization_id"],
        building_id=scope["building_id"], zone_id=scope["database_zone_id"], limit=1)
    assert saved[0].observed_at == observation.observed_at
    assert saved[0].source == observation.source
    assert saved[0].simulated is True and saved[0].quality_state == "VALID"
    assert saved[0].signal.value == "temperature"


@pytest.mark.parametrize("status", ["UNMAPPED", "SUGGESTED", "REJECTED", "INACTIVE"])
def test_only_confirmed_mapping_is_ingestible(ingestion_context, status):
    context = ingestion_context
    with context["sessions"].begin() as session:
        from backend.database.models import PointMappingRecord
        point = session.get(PointMappingRecord, context["ids"][2])
        point.mapping_status = status
    result = context["service"].ingest(_observation(context))
    assert result.accepted is False
    assert result.reason_code == f"MAPPING_{status}"


def test_duplicate_observation_is_idempotent(ingestion_context):
    context = ingestion_context
    observation = _observation(context)
    first = context["service"].ingest(observation)
    second = context["service"].ingest(observation)
    assert first.persisted is True
    assert second.accepted is True and second.duplicate is True and second.persisted is False
    scope = context["configuration"].telemetry_scope(str(context["ids"][3]))
    rows = context["telemetry"].list_zone(organization_id=scope["organization_id"],
        building_id=scope["building_id"], zone_id=scope["database_zone_id"], limit=10)
    assert len(rows) == 1


def test_mapping_to_zone_in_another_building_is_rejected(ingestion_context):
    context = ingestion_context
    other_org, other_building, other_floor, other_zone = [uuid4() for _ in range(4)]
    with context["sessions"].begin() as session:
        session.add(OrganizationRecord(organization_id=other_org, name="Other", slug=f"other-{uuid4().hex[:8]}"))
        session.add(BuildingRecord(building_id=other_building, organization_id=other_org,
            name="Other building", slug=f"other-{uuid4().hex[:8]}", building_key=f"other-{uuid4().hex[:8]}", address={}))
        session.add(FloorRecord(floor_id=other_floor, building_id=other_building, name="Other floor", floor_key="floor"))
        session.add(ZoneRecord(zone_id=other_zone, floor_id=other_floor, zone_key="other-room",
            name="Other room", zone_type="classroom", capacity=20, area_m2=30,
            comfort_min_c=19, comfort_max_c=26))
        from backend.database.models import PointMappingRecord
        session.get(PointMappingRecord, context["ids"][2]).zone_id = other_zone
    result = context["service"].ingest(_observation(context))
    assert result.accepted is False and result.reason_code == "OWNERSHIP_CHAIN_INVALID"


def test_invalid_zone_mapping_and_unreadable_point_fail_closed(ingestion_context):
    context = ingestion_context
    with context["sessions"].begin() as session:
        from backend.database.models import PointMappingRecord
        point = session.get(PointMappingRecord, context["ids"][2])
        point.zone_id = None
    assert context["service"].ingest(_observation(context)).reason_code == "MAPPING_OR_OWNERSHIP_NOT_FOUND"
    with context["sessions"].begin() as session:
        point = session.get(PointMappingRecord, context["ids"][2])
        point.zone_id = context["ids"][3]
        point.readable = False
        point.writable = True
    assert context["service"].ingest(_observation(context)).reason_code == "POINT_NOT_READABLE"


def test_quality_gate_rejects_invalid_nonfinite_and_stale_values(ingestion_context):
    context = ingestion_context
    try:
        _observation(context, value=float("nan"))
        assert False, "non-finite provider value must be rejected by schema"
    except ValidationError:
        pass
    context["service"].data_quality = DataQualityGate(max_age_seconds={"temperature": 1})
    stale = _observation(context, observed_at=datetime.now(timezone.utc) - timedelta(seconds=30))
    result = context["service"].ingest(stale)
    assert result.accepted is False and result.quality_state == QualityState.STALE
    upstream = _observation(context, quality_state=QualityState.INVALID)
    assert context["service"].ingest(upstream).quality_state == QualityState.INVALID


def test_required_timestamp_and_timezone_are_enforced():
    with pytest.raises(ValidationError):
        ProviderObservation(integration_id="i", device_id="d", point_mapping_id="p", value=1,
            source="test", simulated=True)
    with pytest.raises(ValidationError):
        ProviderObservation(integration_id="i", device_id="d", point_mapping_id="p",
            observed_at=datetime(2026, 1, 1), value=1, source="test", simulated=True)
    with pytest.raises(ValidationError):
        ProviderObservation(integration_id="i", device_id="d", point_mapping_id="p",
            observed_at=datetime.now(timezone.utc), value=True, source="test", simulated=True)
    with pytest.raises(ValidationError):
        ProviderObservation(integration_id="i", device_id="d", point_mapping_id="p",
            observed_at=datetime.now(timezone.utc), value=1, source="test", simulated=True, image="frame")


def test_occupancy_count_and_signal_unit_are_structurally_validated(ingestion_context):
    context = ingestion_context
    with context["sessions"].begin() as session:
        from backend.database.models import PointMappingRecord
        point = session.get(PointMappingRecord, context["ids"][2])
        point.logical_signal = "occupancy"
        point.data_type = "integer"
        point.unit = "people"
    fractional = context["service"].ingest(_observation(context, value=1.5))
    assert fractional.accepted is False and fractional.reason_code == "OCCUPANCY_COUNT_NOT_INTEGER"
    over_capacity = context["service"].ingest(_observation(context, value=21))
    assert over_capacity.accepted is False and over_capacity.quality_state == QualityState.OUT_OF_RANGE
    with context["sessions"].begin() as session:
        from backend.database.models import PointMappingRecord
        session.get(PointMappingRecord, context["ids"][2]).unit = "kW"
    invalid_unit = context["service"].ingest(_observation(context, value=1))
    assert invalid_unit.accepted is False and invalid_unit.reason_code == "POINT_UNIT_INCOMPATIBLE"


def test_negative_power_is_rejected_without_control_side_effect(ingestion_context):
    context = ingestion_context
    with context["sessions"].begin() as session:
        from backend.database.models import PointMappingRecord
        point = session.get(PointMappingRecord, context["ids"][2])
        point.logical_signal = "power"
        point.unit = "kW"
    result = context["service"].ingest(_observation(context, value=-0.2))
    assert result.accepted is False and result.quality_state == QualityState.OUT_OF_RANGE
    assert not hasattr(context["service"], "control_provider")


def test_explicit_runtime_input_requires_consumer_and_does_not_happen_by_default(ingestion_context):
    context = ingestion_context
    ordinary = context["service"].ingest(_observation(context))
    assert ordinary.accepted and not ordinary.runtime_input_applied
    requested = context["service"].ingest(_observation(context, runtime_input=True))
    assert requested.accepted is False and requested.reason_code == "RUNTIME_CONSUMER_UNAVAILABLE"
    class Consumer:
        def apply_current_observation(self, **payload):
            self.payload = payload
            return True
    consumer = Consumer()
    context["service"].runtime_consumer = consumer
    applied = context["service"].ingest(_observation(context, runtime_input=True))
    assert applied.accepted and applied.runtime_input_applied
    assert consumer.payload["simulated"] is True


def test_simulated_provider_marks_explicit_values_as_simulated(ingestion_context):
    context = ingestion_context
    with context["sessions"]() as session:
        from backend.database.models import PointMappingRecord
        point = session.get(PointMappingRecord, context["ids"][2])
        emitted = ExplicitValueSimulatedProvider().observations(
            integration_id=str(context["ids"][0]), device_id=str(context["ids"][1]),
            points=[point], values={str(point.point_mapping_id): 22.0}, observed_at=datetime.now(timezone.utc))
    assert len(emitted) == 1 and emitted[0].simulated is True
    assert emitted[0].value == 22.0

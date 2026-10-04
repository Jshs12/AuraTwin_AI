from datetime import datetime, timedelta, timezone
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from backend.core.mock_providers import (MockEnergyProvider, MockOccupancyProvider,
    MockTariffProvider, MockTemperatureProvider)
from backend.database.configuration import SQLAlchemyConfigurationRepository
from backend.database.engine import create_session_factory
from backend.database.models import (Base, BuildingRecord, DeviceRecord, FloorRecord,
    IntegrationRecord, OrganizationRecord, PointMappingRecord, ZoneRecord)
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider
from backend.schemas.data_quality import QualityState
from backend.schemas.provider_observation import ProviderObservation
from backend.services.data_quality import DataQualityGate
from backend.services.zone_state import ZoneStateService
from backend.telemetry.ingestion import ProviderObservationIngestionService
from backend.telemetry.repository import SQLAlchemyTelemetryRepository
from backend.telemetry.runtime_consumer import ZoneStateRuntimeConsumer
from backend.telemetry.service import TelemetryPersistenceService
from backend.core.monitoring import ZoneMonitoringScheduler


@pytest.fixture
def runtime_context(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    organization_id, building_id, floor_id, zone_id = [uuid4() for _ in range(4)]
    integration_id, device_id, point_id = [uuid4() for _ in range(3)]
    zone_key = "room-a1"
    legacy_zone_id = "runtime_zone_a1"
    with sessions.begin() as session:
        session.add(OrganizationRecord(organization_id=organization_id, name="Runtime Org",
            slug=f"org-{uuid4().hex[:8]}"))
        session.add(BuildingRecord(building_id=building_id, organization_id=organization_id,
            name="Runtime Building", slug=f"building-{uuid4().hex[:8]}",
            building_key=f"building-{uuid4().hex[:8]}", address={}))
        session.add(FloorRecord(floor_id=floor_id, building_id=building_id, name="Floor",
            floor_key="floor-a"))
        session.add(ZoneRecord(zone_id=zone_id, floor_id=floor_id, zone_key=zone_key,
            legacy_zone_id=legacy_zone_id, name="Room A1", zone_type="classroom", capacity=20,
            area_m2=40, comfort_min_c=19, comfort_max_c=26))
        session.add(IntegrationRecord(integration_id=integration_id, building_id=building_id,
            name="simulated", integration_type="BACNET", status="CONFIGURED", configuration={}))
        session.add(DeviceRecord(device_id=device_id, integration_id=integration_id,
            external_device_id="controller-a1", name="Controller", device_type="HVAC", status="CONFIGURED"))
        session.add(PointMappingRecord(point_mapping_id=point_id, device_id=device_id, zone_id=zone_id,
            external_point_id="AI:3", logical_signal="temperature", data_type="number", unit="C",
            readable=True, writable=False, metadata_json={}, mapping_status="CONFIRMED", mapping_source="OPERATOR"))
    configuration = SQLAlchemyConfigurationRepository(sessions)
    quality = DataQualityGate()
    control = SimulatedBACnetBuildingControlProvider(zone_ids=[legacy_zone_id])
    state = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
        MockEnergyProvider(MockTariffProvider()), control, data_quality_gate=quality,
        configuration_repository=configuration)
    telemetry = TelemetryPersistenceService(SQLAlchemyTelemetryRepository(sessions), configuration)
    ingestion = ProviderObservationIngestionService(sessions, telemetry, data_quality=quality,
        runtime_consumer=ZoneStateRuntimeConsumer(state))
    yield {"engine": engine, "sessions": sessions, "ids": (integration_id, device_id, point_id, zone_id),
        "scope": (str(organization_id), str(building_id), str(floor_id)), "state": state,
        "ingestion": ingestion, "quality": quality, "telemetry": telemetry,
        "configuration": configuration, "zone_id": legacy_zone_id}
    engine.dispose()


def observation(context, *, signal="temperature", value=22.5, runtime_input=True, observed_at=None):
    integration_id, device_id, point_id, _ = context["ids"]
    with context["sessions"].begin() as session:
        point = session.get(PointMappingRecord, point_id)
        point.logical_signal = signal
        point.unit = {"occupancy": "people", "temperature": "C", "cooling_setpoint": "C",
                      "power": "kW", "energy": "kWh", "tariff_rate": "USD/kWh"}[signal]
    return ProviderObservation(integration_id=str(integration_id), device_id=str(device_id),
        point_mapping_id=str(point_id), observed_at=observed_at or datetime.now(timezone.utc),
        value=float(value), source="phase_11_7_simulated_provider", simulated=True,
        runtime_input=runtime_input)


@pytest.mark.parametrize(("signal", "value", "expected"), [
    ("occupancy", 6, "occupancy"),
    ("temperature", 22.5, "temperature"),
    ("cooling_setpoint", 23.0, "setpoint"),
])
def test_explicit_current_signals_update_existing_zone_state(runtime_context, signal, value, expected):
    ctx = runtime_context
    result = ctx["ingestion"].ingest(observation(ctx, signal=signal, value=value))
    assert result.accepted and result.persisted and result.runtime_input_applied
    state = ctx["state"].get_zone_state(ctx["zone_id"], persist=False)
    if expected == "occupancy":
        assert state.occupancy.people_count == value
        assert state.occupancy.source == "phase_11_7_simulated_provider"
        assert state.occupancy.simulated is True
        assert state.data_quality.signals["occupancy"].state == QualityState.VALID
    elif expected == "temperature":
        assert state.temperature == value
        assert state.temperature_source == "phase_11_7_simulated_provider"
        assert state.temperature_simulated is True
        assert state.data_quality.signals["temperature"].state == QualityState.VALID
    else:
        assert state.hvac_status.present_value == value
        assert state.hvac_status.setpoint_source == "phase_11_7_simulated_provider"
        assert state.hvac_status.setpoint_simulated is True
        assert state.data_quality.signals["hvac_setpoint"].state == QualityState.VALID


def test_historical_telemetry_does_not_update_runtime_zone_state(runtime_context):
    ctx = runtime_context
    before = ctx["state"].get_zone_state(ctx["zone_id"], persist=False).temperature
    result = ctx["ingestion"].ingest(observation(ctx, value=20.5, runtime_input=False))
    after = ctx["state"].get_zone_state(ctx["zone_id"], persist=False)
    assert result.accepted and result.persisted and not result.runtime_input_applied
    assert after.temperature == before


def test_energy_signals_remain_historical_even_when_runtime_was_requested(runtime_context):
    ctx = runtime_context
    before = ctx["state"].get_zone_state(ctx["zone_id"], persist=False).energy.power_kw
    result = ctx["ingestion"].ingest(observation(ctx, signal="power", value=4.2, runtime_input=True))
    after = ctx["state"].get_zone_state(ctx["zone_id"], persist=False)
    assert result.accepted and result.persisted and not result.runtime_input_applied
    assert result.reason_code == "SIGNAL_HISTORICAL_ONLY"
    assert after.energy.power_kw == before


def test_stale_runtime_observation_is_rejected_by_existing_quality_policy(runtime_context):
    ctx = runtime_context
    gate = DataQualityGate(max_age_seconds={"temperature": 1})
    ctx["ingestion"].data_quality = gate
    ctx["state"].data_quality_gate = gate
    result = ctx["ingestion"].ingest(observation(ctx, value=20,
        observed_at=datetime.now(timezone.utc) - timedelta(seconds=10)))
    assert not result.accepted and result.quality_state == QualityState.STALE
    assert ctx["state"].get_zone_state(ctx["zone_id"], persist=False).temperature == 27.1


def test_runtime_consumer_rejects_cross_organization_scope(runtime_context):
    ctx = runtime_context
    organization_id, building_id, floor_id = ctx["scope"]
    applied = ctx["state"].apply_runtime_observation(organization_id=str(uuid4()),
        building_id=building_id, floor_id=floor_id, zone_id=str(ctx["ids"][3]),
        zone_key="room-a1", signal="temperature", value=21,
        observed_at=datetime.now(timezone.utc), source="test", simulated=True,
        quality_state=QualityState.VALID)
    assert not applied
    assert ctx["state"].get_zone_state(ctx["zone_id"], persist=False).temperature == 27.1


def test_runtime_consumer_rejects_invalid_or_unknown_quality(runtime_context):
    ctx = runtime_context
    organization_id, building_id, floor_id = ctx["scope"]
    applied = ctx["state"].apply_runtime_observation(organization_id=organization_id,
        building_id=building_id, floor_id=floor_id, zone_id=str(ctx["ids"][3]),
        zone_key="room-a1", signal="temperature", value=21,
        observed_at=datetime.now(timezone.utc), source="test", simulated=True,
        quality_state=QualityState.INVALID)
    assert not applied


def test_monitoring_scheduler_accepts_any_configured_zone_count():
    scheduler = ZoneMonitoringScheduler(state_service=SimpleNamespace(_zones={}), workflow=None,
        control_service=None, yolo_provider=None)
    configured_scope = [f"building_zone_{index}" for index in range(13)]
    scheduler.configure_zones(configured_scope)
    assert scheduler.monitored_zones == configured_scope
    assert len(scheduler.get_status()["zones"]) == 13


def test_phase_13_3_out_of_order_observation_cannot_replace_newer_runtime_state(runtime_context):
    ctx = runtime_context
    policy = DataQualityGate(max_age_seconds={}, ranges={}, future_clock_skew_seconds=0)
    ctx["ingestion"].data_quality = policy
    ctx["state"].data_quality_gate = policy
    newer_at = datetime.now(timezone.utc)
    accepted = ctx["ingestion"].ingest(observation(ctx, value=21.5, observed_at=newer_at))
    assert accepted.runtime_input_applied is True
    older = ctx["ingestion"].ingest(observation(ctx, value=24.0,
        observed_at=newer_at - timedelta(seconds=1)))
    assert older.accepted is True  # valid for history under the configured policy
    assert older.runtime_applicable is True
    assert older.runtime_input_applied is False
    assert older.reason_code == "RUNTIME_STATE_REJECTED"
    assert ctx["state"].get_zone_state(ctx["zone_id"], persist=False).temperature == 21.5


def test_phase_13_3_duplicate_does_not_reapply_runtime(runtime_context):
    ctx = runtime_context
    current = observation(ctx, value=22.0)
    first = ctx["ingestion"].ingest(current)
    duplicate = ctx["ingestion"].ingest(current)
    assert first.runtime_input_applied is True
    assert duplicate.accepted and duplicate.duplicate
    assert duplicate.runtime_applicable is True
    assert duplicate.runtime_input_applied is False


def test_phase_13_3_non_runtime_meter_signal_is_history_only(runtime_context):
    ctx = runtime_context
    result = ctx["ingestion"].ingest(observation(ctx, signal="energy", value=150.0))
    assert result.accepted and result.persisted
    assert result.runtime_applicable is False
    assert result.runtime_input_applied is False
    assert ctx["state"].get_zone_state(ctx["zone_id"], persist=False).energy.energy_kwh != 150.0


def test_phase_13_3_ingestion_preserves_resolved_scope_unit_and_ingested_time(runtime_context):
    ctx = runtime_context
    result = ctx["ingestion"].ingest(observation(ctx, value=22.0))
    assert result.accepted and result.persisted
    assert result.organization_id == ctx["scope"][0]
    assert result.building_id == ctx["scope"][1]
    assert result.floor_id == ctx["scope"][2]
    assert result.integration_id == str(ctx["ids"][0])
    assert result.device_id == str(ctx["ids"][1])
    assert result.point_mapping_id == str(ctx["ids"][2])
    assert result.protocol == "BACNET"
    assert result.unit == "C"
    assert result.observed_at is not None and result.ingested_at is not None
    assert result.simulated is True and result.source == "phase_11_7_simulated_provider"


def test_phase_13_3_provider_hints_must_match_confirmed_mapping(runtime_context):
    ctx = runtime_context
    bad_signal = observation(ctx).model_copy(update={"signal": "occupancy"})
    result = ctx["ingestion"].ingest(bad_signal)
    assert result.accepted is False and result.reason_code == "PROVIDER_SIGNAL_MISMATCH"


def test_phase_13_3_runtime_ingestion_never_calls_hvac_write(runtime_context, monkeypatch):
    ctx = runtime_context
    writes = []
    monkeypatch.setattr(ctx["state"].control_provider, "write_setpoint",
                        lambda command: writes.append(command))
    result = ctx["ingestion"].ingest(observation(ctx, value=22.2))
    assert result.runtime_input_applied is True
    assert writes == []


def test_phase_13_3_quality_events_use_existing_event_trace(runtime_context):
    from backend.core.events import EventTrace
    ctx = runtime_context
    EventTrace.clear()
    ctx["ingestion"].ingest(observation(ctx, value=22.0))
    types = [event.event_type for event in EventTrace.get_history(str(ctx["ids"][3]))]
    assert "OBSERVATION_RECEIVED" in types
    assert "OBSERVATION_ACCEPTED" in types

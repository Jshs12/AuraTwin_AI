from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from backend.core.time import utc_now
from backend.database.configuration import SQLAlchemyConfigurationRepository
from backend.database.engine import create_database_engine, create_session_factory
from backend.database.models import OptimizationIntervalRecord
from backend.database.runtime import bootstrap_legacy_demo_configuration, upgrade_schema
from backend.optimization.interval_repository import SQLAlchemyOptimizationIntervalRepository
from backend.schemas.data_quality import QualityState
from backend.schemas.telemetry import TelemetryObservation, TelemetrySignal
from backend.services.data_quality import DataQualityGate
from backend.services.optimization_intervals import OptimizationIntervalService
from sqlalchemy.exc import IntegrityError
from backend.telemetry.repository import SQLAlchemyTelemetryRepository
from backend.telemetry.service import TelemetryPersistenceService
from tests.unit.test_simulated_bacnet_control import make_state


@pytest.fixture
def interval_context():
    engine = create_database_engine(url="sqlite+pysqlite:///:memory:")
    upgrade_schema(engine)
    sessions = create_session_factory(engine)
    bootstrap_legacy_demo_configuration(sessions)
    config = SQLAlchemyConfigurationRepository(sessions)
    telemetry = TelemetryPersistenceService(SQLAlchemyTelemetryRepository(sessions), config)
    repository = SQLAlchemyOptimizationIntervalRepository(sessions)
    yield config, telemetry, repository
    engine.dispose()


def _state(count: int, observed_at=None, *, energy=20.0, tariff=0.12):
    observed_at = observed_at or utc_now()
    state = make_state()
    state.zone = state.zone.model_copy(update={"zone_id": "classroom_01"})
    state.occupancy = state.occupancy.model_copy(update={
        "zone_id": "classroom_01", "people_count": count, "capacity": 30,
        "occupancy_percentage": count / 30 * 100,
        "occupancy_state": "EMPTY" if count == 0 else "LOW" if count / 30 * 100 < 30
        else "MEDIUM" if count / 30 * 100 < 70 else "HIGH",
        "observed_at": observed_at, "timestamp": observed_at,
        "source": "simulated_interval_test", "simulated": True,
    })
    state.temperature_observed_at = observed_at
    state.temperature_source = "simulated_interval_test"
    state.temperature_simulated = True
    state.energy = state.energy.model_copy(update={
        "zone_id": "classroom_01", "energy_kwh": energy, "observed_at": observed_at,
        "source": "simulated_interval_test", "is_simulated": True,
    })
    state.tariff = state.tariff.model_copy(update={
        "rate_per_kwh": tariff, "currency": "USD", "observed_at": observed_at,
        "source": "simulated_interval_test", "simulated": True,
    })
    state.energy.tariff = state.tariff
    state.hvac_status = state.hvac_status.model_copy(update={
        "zone_id": "classroom_01", "observed_at": observed_at, "timestamp": observed_at,
        "setpoint_observed_at": observed_at, "simulated": True,
    })
    state.data_quality = DataQualityGate().assess_zone_state(state)
    return state


def _persist_boundaries(telemetry, config, state):
    scope = config.telemetry_scope(state.zone.zone_id)
    assert scope is not None
    rows = (
        (TelemetrySignal.ENERGY, state.energy.energy_kwh, "kWh", "energy"),
        (TelemetrySignal.TARIFF_RATE, state.tariff.rate_per_kwh, "USD/kWh", "tariff"),
    )
    for signal, value, unit, name in rows:
        assessment = state.data_quality.signals[name]
        telemetry.persist_observation(TelemetryObservation(
            organization_id=scope["organization_id"], building_id=scope["building_id"],
            floor_id=scope["floor_id"], zone_id=scope["database_zone_id"], signal=signal,
            value=value, unit=unit, observed_at=state.energy.observed_at,
            source="simulated_interval_test", quality_state=assessment.state.value, simulated=True,
        ))


def test_active_interval_survives_service_restart_without_control_side_effect(interval_context):
    config, telemetry, repository = interval_context
    service = OptimizationIntervalService(repository, telemetry, config)
    initial = _state(12)
    active = service.start(initial, 22.0, 23.0)

    recovered = OptimizationIntervalService(repository, telemetry, config)
    assert recovered.active("classroom_01").interval_id == active.interval_id
    assert recovered.active("classroom_01").status == "ACTIVE"
    assert recovered.history("classroom_01") == []


def test_interval_repository_rejects_cross_organization_ownership(interval_context):
    config, telemetry, repository = interval_context
    service = OptimizationIntervalService(repository, telemetry, config)
    interval = service.start(_state(12), 22.0, 23.0)
    invalid_org_id = uuid4()
    with pytest.raises(IntegrityError):
        with repository.sessions.begin() as session:
            row = session.get(OptimizationIntervalRecord, UUID(interval.interval_id))
            row.organization_id = invalid_org_id
            session.flush()
    assert repository.active(interval.database_zone_id).organization_id != str(invalid_org_id)


def test_persisted_energy_and_flat_tariff_are_attributed_only_from_persisted_boundaries(interval_context):
    config, telemetry, repository = interval_context
    service = OptimizationIntervalService(repository, telemetry, config)
    start_at = utc_now() - timedelta(minutes=2)
    initial = _state(12, start_at, energy=20.0)
    _persist_boundaries(telemetry, config, initial)
    interval = service.start(initial, 22.0, 23.0, started_at=start_at)

    end_at = utc_now()
    ending = _state(7, end_at, energy=20.75)
    _persist_boundaries(telemetry, config, ending)
    can_evaluate, completed = service.observe(ending)

    assert can_evaluate
    assert completed.interval_id == interval.interval_id
    assert completed.energy_consumed_kwh == 0.75
    assert completed.energy_status == "AVAILABLE"
    assert completed.cost_consumed == pytest.approx(0.09)
    assert completed.cost_status == "AVAILABLE"
    assert completed.simulated is True
    assert completed.energy_unit == "kWh"
    assert not hasattr(completed, "energy_savings_kwh")
    recovered = OptimizationIntervalService(repository, telemetry, config)
    assert recovered.history("classroom_01")[0].cost_consumed == pytest.approx(0.09)


def test_missing_persisted_energy_boundary_keeps_impact_unavailable(interval_context):
    config, telemetry, repository = interval_context
    service = OptimizationIntervalService(repository, telemetry, config)
    interval = service.start(_state(12), 22.0, 23.0)
    _, completed = service.observe(_state(7))
    assert completed.interval_id == interval.interval_id
    assert completed.energy_consumed_kwh is None
    assert completed.energy_status == "UNAVAILABLE"
    assert completed.energy_reason_code == "START_BOUNDARY_UNAVAILABLE"
    assert completed.cost_consumed is None
    assert completed.cost_status == "UNAVAILABLE"


def test_tariff_change_during_interval_keeps_cost_unavailable(interval_context):
    config, telemetry, repository = interval_context
    service = OptimizationIntervalService(repository, telemetry, config)
    start_at = utc_now() - timedelta(minutes=2)
    initial = _state(12, start_at, energy=20.0)
    _persist_boundaries(telemetry, config, initial)
    service.start(initial, 22.0, 23.0, started_at=start_at)

    scope = config.telemetry_scope("classroom_01")
    middle_at = start_at + timedelta(minutes=1)
    telemetry.persist_observation(TelemetryObservation(
        organization_id=scope["organization_id"], building_id=scope["building_id"],
        floor_id=scope["floor_id"], zone_id=scope["database_zone_id"],
        signal=TelemetrySignal.TARIFF_RATE, value=0.14, unit="USD/kWh", observed_at=middle_at,
        source="simulated_interval_test", quality_state=QualityState.VALID.value, simulated=True,
    ))
    ending = _state(7, utc_now(), energy=20.75, tariff=0.12)
    _persist_boundaries(telemetry, config, ending)
    _, completed = service.observe(ending)
    assert completed.energy_consumed_kwh == 0.75
    assert completed.cost_consumed is None
    assert completed.cost_status == "UNAVAILABLE"
    assert completed.cost_reason_code == "TARIFF_CHANGED_DURING_INTERVAL"

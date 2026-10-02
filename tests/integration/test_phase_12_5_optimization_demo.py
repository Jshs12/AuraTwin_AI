from __future__ import annotations

import pytest
from datetime import timedelta

from backend.core.events import EventTrace
from backend.core.interfaces import OccupancyProvider
from backend.core.time import utc_now
from backend.core.mock_providers import MockEnergyProvider, MockTariffProvider, MockTemperatureProvider
from backend.core.monitoring import ZoneMonitoringScheduler
from backend.database.configuration import SQLAlchemyConfigurationRepository
from backend.database.engine import create_database_engine, create_session_factory
from backend.database.runtime import bootstrap_legacy_demo_configuration, upgrade_schema
from backend.demo.safety_profile import build_demo_safety_profile
from backend.demo.scenario import DemoScenarioEngine
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider
from backend.intelligence.providers import MockIntelligenceProvider
from backend.intelligence.service import RecommendationWorkflow
from backend.optimization.interval_repository import SQLAlchemyOptimizationIntervalRepository
from backend.schemas.events import OccupancyEvent
from backend.services.control import ControlService
from backend.services.control_state import ZoneControlStateService
from backend.services.data_quality import DataQualityGate
from backend.services.optimization_intervals import OptimizationIntervalService
from backend.services.zone_state import ZoneStateService
from backend.telemetry.repository import SQLAlchemyTelemetryRepository
from backend.telemetry.service import TelemetryPersistenceService


class ScenarioOccupancy(OccupancyProvider):
    def __init__(self, count: int = 17):
        self.count = count

    def get_occupancy(self, zone_id: str) -> OccupancyEvent:
        observed_at = utc_now()
        capacity = 40
        percent = self.count / capacity * 100
        level = "EMPTY" if self.count == 0 else "LOW" if percent < 30 else "MEDIUM" if percent < 70 else "HIGH"
        return OccupancyEvent(zone_id=zone_id, people_count=self.count, capacity=capacity,
            occupancy_percentage=percent, occupancy_state=level, timestamp=observed_at,
            observed_at=observed_at, source="phase_12_5_demo_fixture", simulated=True)


class CountingSimulatedProvider(SimulatedBACnetBuildingControlProvider):
    def __init__(self):
        super().__init__(zone_ids=["classroom_01"], initial_temperature=26.0,
                         initial_setpoint=20.0, initial_energy_kwh=100.0)
        self.write_count = 0

    def write_command(self, command):
        self.write_count += 1
        return super().write_command(command)


@pytest.fixture
def persistent_demo_pipeline():
    engine = create_database_engine(url="sqlite+pysqlite:///:memory:")
    upgrade_schema(engine)
    sessions = create_session_factory(engine)
    bootstrap_legacy_demo_configuration(sessions)
    config = SQLAlchemyConfigurationRepository(sessions)
    telemetry = TelemetryPersistenceService(SQLAlchemyTelemetryRepository(sessions), config)
    interval_repository = SQLAlchemyOptimizationIntervalRepository(sessions)
    control_provider = CountingSimulatedProvider()
    occupancy = ScenarioOccupancy()
    state_service = ZoneStateService(occupancy, MockTemperatureProvider(),
        MockEnergyProvider(MockTariffProvider()), control_provider,
        configuration_repository=config, telemetry_service=telemetry)
    zone = state_service._zones["classroom_01"]
    safety = build_demo_safety_profile([zone], control_provider)
    control_provider.command_safety = safety
    controls = ZoneControlStateService(initially_enabled=True)
    control = ControlService(control_provider, safety=safety,
        data_quality=state_service.data_quality_gate,
        state_provider=state_service.get_zone_state, control_states=controls)
    workflow = RecommendationWorkflow(provider=MockIntelligenceProvider(), safety=safety,
        data_quality=state_service.data_quality_gate)
    intervals = OptimizationIntervalService(interval_repository, telemetry, config)
    scheduler = ZoneMonitoringScheduler(state_service, workflow, control, occupancy,
        optimization_interval_service=intervals)
    yield config, telemetry, interval_repository, control_provider, occupancy, state_service, scheduler
    engine.dispose()


def test_demo_safety_profile_is_derived_and_refuses_non_simulated_control():
    simulated = CountingSimulatedProvider()
    zone_state = ZoneStateService(ScenarioOccupancy(), MockTemperatureProvider(),
        MockEnergyProvider(MockTariffProvider()), simulated)
    profile = build_demo_safety_profile([zone_state._zones["classroom_01"]], simulated)
    assert profile.command_policy_status()["ready"] is True
    assert profile.min_setpoint == 22
    assert profile.max_setpoint == 26
    assert profile.max_setpoint_delta == 4

    class NonSimulatedProvider:
        is_simulated = False
        is_ready = True

    with pytest.raises(ValueError, match="requires a simulated"):
        build_demo_safety_profile([zone_state._zones["classroom_01"]], NonSimulatedProvider())


def test_demo_five_x_speed_uses_phase_wall_duration_without_future_timestamps():
    scenario = DemoScenarioEngine(phase_duration_seconds=20)
    scenario.speed_multiplier = 5.0
    scenario.scenario_id = "deterministic-clock-test"
    scenario.phase_number = 2
    scenario.occupancy_provider.capacities = {zone_id: 40 for zone_id in scenario.ZONES}
    scenario.occupancy_provider.set_counts(scenario.ZONES, scenario.PHASES[1][1])
    received = []

    async def on_phase(zone_id, occupancy, scenario_id, elapsed_hours):
        received.append((zone_id, occupancy, scenario_id, elapsed_hours))

    scenario._on_phase = on_phase
    import asyncio
    asyncio.run(scenario._emit_phase())

    assert len(received) == len(scenario.ZONES)
    assert all(item[2] == scenario.scenario_id for item in received)
    assert all(item[3] == pytest.approx(20 / 5 / 3600) for item in received)
    assert all(item[1].simulated is True for item in received)
    assert all(item[1].observed_at <= utc_now() for item in received)


def test_persistent_demo_loop_recommends_controls_holds_and_attributes_cost(persistent_demo_pipeline):
    (config, telemetry, interval_repository, control_provider, occupancy,
     state_service, scheduler) = persistent_demo_pipeline
    EventTrace.clear()

    initial = scheduler.process_simulated_occupancy

    async def run():
        result = await initial("classroom_01", occupancy.get_occupancy("classroom_01"), "phase-12-5", 0)
        assert result["decision"].recommendation_kind == "intelligence"
        assert result["decision"].intelligence_recommendation.provider == "mock_intelligence_provider"
        assert result["decision"].validation.outcome == "VALIDATED"
        assert scheduler.demo_control_activity["classroom_01"]["success"] is True

        interval_service = scheduler.optimization_intervals
        active = interval_service.active("classroom_01")
        assert active is not None and active.status == "ACTIVE"
        assert active.starting_occupancy == 17
        assert active.previous_setpoint == 20
        assert active.optimized_setpoint == 24
        first_command_count = control_provider.write_count
        assert first_command_count == 1

        held = await scheduler.process_simulated_occupancy(
            "classroom_01", occupancy.get_occupancy("classroom_01"), "phase-12-5", 1.0)
        assert held["optimization_holding"] is True
        assert control_provider.write_count == first_command_count
        assert interval_service.active("classroom_01").interval_id == active.interval_id
        assert interval_service.active("classroom_01").started_at == active.started_at

        occupancy.count = 11
        completed_result = await scheduler.process_simulated_occupancy(
            "classroom_01", occupancy.get_occupancy("classroom_01"), "phase-12-5", 0.0)
        completed = interval_service.history("classroom_01")[0]
        assert completed.status == "COMPLETED"
        assert completed.ending_occupancy == 11
        assert completed.energy_status == "AVAILABLE", (completed.energy_reason_code,
            completed.starting_energy_kwh, completed.ending_energy_kwh,
            completed.starting_energy_observed_at, completed.ending_energy_observed_at)
        assert completed.starting_energy_observed_at >= completed.started_at
        assert completed.ending_energy_observed_at > completed.starting_energy_observed_at
        assert completed.energy_consumed_kwh is not None and completed.energy_consumed_kwh > 0
        assert completed.cost_status == "AVAILABLE"
        assert completed.cost_consumed == pytest.approx(completed.energy_consumed_kwh * 0.15)
        assert completed.simulated is True
        assert control_provider.write_count == first_command_count

        # A second advisory is evaluated with the newly observed occupancy;
        # the simulated control path may act only through its normal gates.
        assert completed_result["decision"] is not None
        assert completed_result["decision"].validation.outcome in {"VALIDATED", "FALLBACK", "REJECTED"}

        recovered = OptimizationIntervalService(interval_repository, telemetry, config)
        assert recovered.history("classroom_01")[0].interval_id == completed.interval_id
        write_count_at_recovery = control_provider.write_count
        assert recovered.active("classroom_01") is None
        # Reconstructing the persistence service is read-only; it issues no command.
        assert control_provider.write_count == write_count_at_recovery

    import asyncio
    asyncio.run(run())

    events = [event.event_type for event in EventTrace.get_history("classroom_01")]
    assert "INTELLIGENCE_REQUESTED" in events
    assert "RECOMMENDATION_VALIDATED" in events
    assert "CONTROL_COMMAND" in events
    assert "OPTIMIZATION_STARTED" in events
    assert "OPTIMIZATION_HOLDING" in events
    assert "OCCUPANCY_CHANGED" in events
    assert "OPTIMIZATION_COMPLETED" in events
    assert "ENERGY_IMPACT_CALCULATED" in events
    assert "COST_IMPACT_CALCULATED" in events
    assert "SAVINGS_BASELINE_UNAVAILABLE" in events


def test_demo_completion_closes_final_interval_before_cumulative_counter_reset(persistent_demo_pipeline):
    (_, _, _, control_provider, occupancy, state_service, scheduler) = persistent_demo_pipeline
    EventTrace.clear()

    import asyncio
    async def run():
        await scheduler.process_simulated_occupancy(
            "classroom_01", occupancy.get_occupancy("classroom_01"), "phase-12-5-finish", 0)
        active = scheduler.optimization_intervals.active("classroom_01")
        assert active is not None

        # A deterministic simulated interval advances the existing HVAC model
        # and persists its real emitted cumulative-energy/tariff observation.
        await scheduler.process_simulated_occupancy(
            "classroom_01", occupancy.get_occupancy("classroom_01"), "phase-12-5-finish", 0.25)
        final_state = state_service.get_zone_state(
            "classroom_01", occupancy_override=occupancy.get_occupancy("classroom_01"))
        completed = scheduler.optimization_intervals.close_active(
            "classroom_01", final_state, "DEMO_COMPLETED")

        assert completed is not None
        assert completed.status == "COMPLETED"
        assert completed.starting_energy_observed_at >= completed.started_at
        assert completed.ending_energy_observed_at > completed.starting_energy_observed_at
        assert completed.ending_energy_kwh >= completed.starting_energy_kwh
        assert completed.energy_status == "AVAILABLE"
        assert completed.cost_status == "AVAILABLE"
        assert completed.simulated is True
        assert completed.reason == "DEMO_COMPLETED"
        assert scheduler.optimization_intervals.active("classroom_01") is None

        # Reset happens only after the persistent interval has its final
        # cumulative boundary, so the next run's zeroed counter cannot rewrite it.
        control_provider.reset_simulation()
        assert scheduler.optimization_intervals.history("classroom_01")[0].interval_id == completed.interval_id

    asyncio.run(run())


def test_invalid_energy_boundary_still_fails_attribution(persistent_demo_pipeline):
    (_, telemetry, _, _, occupancy, state_service, scheduler) = persistent_demo_pipeline
    import asyncio

    async def run():
        await scheduler.process_simulated_occupancy(
            "classroom_01", occupancy.get_occupancy("classroom_01"), "phase-12-5-invalid", 0)
        active = scheduler.optimization_intervals.active("classroom_01")
        assert active is not None

        changed = occupancy.get_occupancy("classroom_01").model_copy(update={
            "people_count": occupancy.count - 1,
            "occupancy_percentage": (occupancy.count - 1) / 40 * 100,
            "occupancy_state": "MEDIUM",
            "observed_at": utc_now(), "timestamp": utc_now(),
        })
        ending = state_service.get_zone_state("classroom_01", occupancy_override=changed)
        ending.energy = ending.energy.model_copy(update={
            "energy_kwh": max(0.0, active.starting_energy_kwh - 1),
            "observed_at": utc_now(), "source": active.energy_source,
            "is_simulated": True,
        })
        ending.data_quality = state_service.data_quality_gate.assess_zone_state(ending)
        telemetry.persist_zone_state(ending)
        _, completed = scheduler.optimization_intervals.observe(ending)
        assert completed.energy_status == "INVALID"
        assert completed.energy_reason_code == "CUMULATIVE_ENERGY_DECREASED"

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["stale", "provenance"])
def test_stale_or_incompatible_energy_still_fails_attribution(persistent_demo_pipeline, failure):
    (_, telemetry, _, control_provider, occupancy, state_service, scheduler) = persistent_demo_pipeline
    import asyncio

    async def run():
        await scheduler.process_simulated_occupancy(
            "classroom_01", occupancy.get_occupancy("classroom_01"), f"phase-12-5-{failure}", 0)
        active = scheduler.optimization_intervals.active("classroom_01")
        assert active is not None and active.starting_energy_kwh is not None

        # Emit a later cumulative sample through the simulated HVAC model.
        control_provider.advance_simulation("classroom_01", occupancy.count, 0.25)
        occupancy.count -= 1
        changed = occupancy.get_occupancy("classroom_01")
        ending = state_service.get_zone_state("classroom_01", occupancy_override=changed)
        if failure == "stale":
            gate = DataQualityGate(max_age_seconds={"energy": 1})
            state_service.data_quality_gate = gate
            ending.energy = ending.energy.model_copy(update={
                "observed_at": utc_now() - timedelta(minutes=2),
            })
        else:
            ending.energy = ending.energy.model_copy(update={
                "source": "different_simulated_energy_source",
            })
        ending.data_quality = state_service.data_quality_gate.assess_zone_state(ending)
        telemetry.persist_zone_state(ending)
        _, completed = scheduler.optimization_intervals.observe(ending)

        assert completed.energy_status in {"UNAVAILABLE", "INVALID"}
        assert completed.energy_consumed_kwh is None
        assert completed.cost_status == "UNAVAILABLE"
        if failure == "stale":
            assert completed.energy_reason_code == "ENDING_BOUNDARY_UNAVAILABLE"
        else:
            assert completed.energy_reason_code == "ENERGY_PROVENANCE_INCOMPATIBLE"

    asyncio.run(run())


def test_failed_simulated_provider_does_not_create_an_optimization_interval(persistent_demo_pipeline):
    (_, _, _, control_provider, occupancy, _, scheduler) = persistent_demo_pipeline
    EventTrace.clear()
    control_provider.failure_mode = "acknowledgement"

    import asyncio
    async def run():
        outcome = await scheduler.process_simulated_occupancy(
            "classroom_01", occupancy.get_occupancy("classroom_01"), "phase-12-5-failure", 0)
        assert outcome["decision"].validation.outcome == "VALIDATED"
        assert scheduler.demo_control_activity["classroom_01"]["success"] is False
        assert scheduler.optimization_intervals.active("classroom_01") is None
        assert scheduler.optimization_intervals.history("classroom_01") == []
        assert control_provider.write_count == 1
    asyncio.run(run())

    event_types = [event.event_type for event in EventTrace.get_history("classroom_01")]
    assert "PROVIDER_FAILURE" in event_types
    assert "FAIL_SAFE_ACTIVATED" in event_types
    assert "OPTIMIZATION_STARTED" not in event_types


def test_authenticated_demo_installs_and_removes_its_isolated_safety_profile(monkeypatch):
    from backend.api.main import (app, control_prov, control_service,
        recommendation_workflow, zone_state_service)
    from tests.security_test_utils import make_client

    zones = tuple(app.state.demo_scenario.ZONES)
    original_modes = {zone_id: control_service.control_states.snapshot(zone_id).control_enabled
                      for zone_id in zones}
    original_policy = control_service.safety
    scheduler = app.state.monitoring_scheduler
    monkeypatch.setattr(scheduler, "optimization_intervals", OptimizationIntervalService())
    monkeypatch.setattr(zone_state_service, "telemetry_service", None)

    client = make_client()
    try:
        assert client.post("/api/demo/reset").status_code == 200
        started = client.post("/api/demo/start")
        assert started.status_code == 200, started.text
        assert client.get("/api/demo/status").json()["safety_profile"] == "SIMULATED_COMFORT_BOUNDED"
        assert scheduler.workflow.provider.provider_name == "mock_intelligence_provider"
        assert scheduler.workflow.safety is control_prov.command_safety
        assert scheduler.control_service.safety is scheduler.workflow.safety
        assert control_service.safety is original_policy
        assert scheduler.workflow is not recommendation_workflow
        assert control_prov.command_safety is not None

        stopped = client.post("/api/demo/stop")
        assert stopped.status_code == 200
        assert app.state.demo_safety_profile is None
        assert control_prov.command_safety is None
        assert scheduler.workflow is recommendation_workflow
        assert scheduler.control_service is control_service
        assert client.post("/api/demo/reset").status_code == 200
    finally:
        if app.state.demo_scenario.status.value in {"RUNNING", "PAUSED"}:
            client.post("/api/demo/stop")
        client.post("/api/demo/reset")
        client.close()
    assert {zone_id: control_service.control_states.snapshot(zone_id).control_enabled
            for zone_id in zones} == original_modes

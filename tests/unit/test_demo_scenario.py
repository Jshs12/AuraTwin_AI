import asyncio
import json


from backend.core.events import EventBroadcaster, EventTrace
from backend.core.mock_providers import MockEnergyProvider, MockOccupancyProvider, MockTariffProvider, MockTemperatureProvider
from backend.core.monitoring import ZoneMonitoringScheduler
from backend.demo.scenario import DemoScenarioEngine, DemoScenarioOccupancyProvider, ScenarioStatus
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider
from backend.intelligence.providers import IntelligenceProvider
from backend.intelligence.schemas import IntelligenceContext, IntelligenceRecommendation
from backend.intelligence.service import RecommendationWorkflow
from backend.services.control import ControlService
from backend.services.zone_state import ZoneStateService


def test_demo_occupancy_provider_is_deterministic_and_marked_as_simulation_input():
    provider = DemoScenarioOccupancyProvider({"classroom_01": 40})
    provider.set_counts(("classroom_01",), (35,))
    first = provider.get_occupancy("classroom_01")
    second = provider.get_occupancy("classroom_01")
    assert first.people_count == second.people_count == 35
    assert first.occupancy_state == "HIGH"
    assert first.occupancy_percentage == 87.5


def test_invalid_demo_phase_duration_uses_safe_default(monkeypatch):
    for value in ("nan", "inf", "-1", "invalid"):
        monkeypatch.setenv("DEMO_PHASE_DURATION_SECONDS", value)
        assert DemoScenarioEngine().phase_duration_seconds == 20.0


def test_scenario_transitions_pause_resume_stop_reset_and_speed():
    async def run():
        engine = DemoScenarioEngine(phase_duration_seconds=1.5)
        emitted = []

        async def on_phase(zone_id, occupancy, scenario_id, elapsed_hours):
            emitted.append((zone_id, occupancy.people_count, scenario_id, elapsed_hours))

        caps = dict(zip(engine.ZONES, (40, 50, 70, 60)))
        status = await engine.start(on_phase, caps)
        assert status["status"] == ScenarioStatus.RUNNING.value
        assert len(emitted) == 4
        scenario_id = status["scenario_id"]
        original_task = engine._task
        await engine.pause()
        assert original_task.done()
        assert engine._task is None
        paused_elapsed = engine.elapsed_seconds
        await asyncio.sleep(0.35)
        assert engine.elapsed_seconds == paused_elapsed
        await engine.resume()
        resumed_task = engine._task
        assert resumed_task is not original_task
        await engine.set_speed(5)
        await asyncio.sleep(0.65)
        assert engine.phase_number >= 2
        assert all(item[2] == scenario_id for item in emitted)
        assert all(item[3] >= 0 for item in emitted)
        await engine.stop()
        assert engine.status == ScenarioStatus.STOPPED
        reset = await engine.reset()
        assert reset["status"] == ScenarioStatus.IDLE.value
        assert reset["phase_number"] == 0
        assert reset["scenario_id"] is None
    asyncio.run(run())


def test_scenario_visits_all_four_phases_with_authoritative_occupancy_values():
    async def run():
        engine = DemoScenarioEngine(phase_duration_seconds=0.2)
        await engine.set_speed(5)
        snapshots = []
        elapsed_values = []
        completions = []
        current = {}

        async def on_phase(zone_id, occupancy, scenario_id, elapsed_hours):
            if zone_id == engine.ZONES[-1]:
                elapsed_values.append(elapsed_hours)
            current[zone_id] = occupancy.people_count
            if zone_id == engine.ZONES[-1]:
                snapshots.append(tuple(current[zone] for zone in engine.ZONES))

        capacities = dict(zip(engine.ZONES, (40, 50, 70, 60)))
        await engine.start(on_phase, capacities, on_complete=lambda: completions.append(True))
        for _ in range(20):
            if engine.status == ScenarioStatus.COMPLETED:
                break
            await asyncio.sleep(0.1)
        assert engine.status == ScenarioStatus.COMPLETED
        assert completions == [True]
        assert snapshots == [phase[1] for phase in engine.PHASES]
        assert [phase[0] for phase in engine.PHASES] == [
            "LOW OCCUPANCY", "OCCUPANCY RISE", "HIGH OCCUPANCY", "OCCUPANCY FALL",
        ]
        assert elapsed_values == [0.0] + [
            engine.phase_duration_seconds / engine.speed_multiplier / 3600.0
        ] * 3
        await engine.reset()
    asyncio.run(run())


def test_demo_completion_stops_the_monitoring_scheduler():
    async def run():
        _, state_service, _, scheduler = _pipeline()
        scheduler.demo_mode = True
        scheduler.start()
        engine = DemoScenarioEngine(phase_duration_seconds=0.2)
        await engine.set_speed(5)
        capacities = {zone: state_service._zones[zone].capacity for zone in engine.ZONES}

        async def stop_scheduler():
            scheduler.demo_mode = False
            await scheduler.stop_and_wait()

        await engine.start(scheduler.process_simulated_occupancy, capacities, on_complete=stop_scheduler)
        scheduler_task = scheduler._task
        deadline = asyncio.get_running_loop().time() + 3
        while engine.status != ScenarioStatus.COMPLETED and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.03)
        assert engine.status == ScenarioStatus.COMPLETED
        assert scheduler._running is False
        assert scheduler._task is None
        await asyncio.sleep(0)
        assert scheduler_task is not None and scheduler_task.done()
        await engine.reset()

    asyncio.run(run())


def test_rapid_pause_resume_keeps_a_single_scenario_loop():
    async def run():
        engine = DemoScenarioEngine(phase_duration_seconds=5.0)
        capacities = dict(zip(engine.ZONES, (40, 50, 70, 60)))

        async def on_phase(*_args):
            return None

        await engine.start(on_phase, capacities)
        await engine.pause()
        await engine.resume()
        await asyncio.sleep(0.3)
        # A duplicated loop would advance in two 0.25 s increments per tick.
        assert engine.elapsed_seconds <= 0.3
        active_scenario_tasks = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and task.get_coro().__qualname__.endswith("DemoScenarioEngine._run")
            and not task.done()
        ]
        assert active_scenario_tasks == [engine._task]
        await engine.stop()
        assert engine._task is None
        await engine.reset()

    asyncio.run(run())


def _pipeline(failure_mode=None, intelligence=None):
    control = SimulatedBACnetBuildingControlProvider(failure_mode=failure_mode)
    state_service = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
                                     MockEnergyProvider(MockTariffProvider()), control)
    workflow = RecommendationWorkflow(provider=intelligence)
    service = ControlService(control)
    scheduler = ZoneMonitoringScheduler(state_service, workflow, service, MockOccupancyProvider())
    return control, state_service, service, scheduler


class UnsafeAdvisoryProvider(IntelligenceProvider):
    provider_name = "unsafe_test_provider"

    def generate_recommendation(self, context: IntelligenceContext):
        return IntelligenceRecommendation(
            zone_id=context.zone_id, recommended_setpoint=30.0,
            rationale="Test unsafe proposal", confidence=0.99,
            provider=self.provider_name, model_source="unit_test",
            context_reference={"zone_id": context.zone_id}, action_type="setpoint_adjustment",
        )


def test_simulated_input_runs_recommendation_safety_fallback_control_hvac_energy_and_events():
  async def run():
    EventTrace.clear()
    control, state_service, service, scheduler = _pipeline(intelligence=UnsafeAdvisoryProvider())
    scheduler.demo_mode = True
    engine = DemoScenarioEngine()
    occupancy = engine.occupancy_provider
    occupancy.capacities = {zone: state_service._zones[zone].capacity for zone in engine.ZONES}
    occupancy.set_counts(engine.ZONES, (35, 50, 65, 55))
    scenario_id = "scenario-unit-test"
    websocket_messages = []

    async def receive(message):
        websocket_messages.append(json.loads(message))

    EventBroadcaster.subscribe(receive)
    assert not hasattr(engine, "control_provider")

    outcome = await scheduler.process_simulated_occupancy(
        "classroom_01", occupancy.get_occupancy("classroom_01"), scenario_id,
    )

    assert outcome["decision"].recommendation_kind == "deterministic_fallback"
    assert outcome["decision"].validation.outcome == "FALLBACK"
    result = service.last_results["classroom_01"]
    assert result.success is True
    assert result.simulated is True
    current = control.read_control_state("classroom_01")
    assert current.present_value == result.applied_setpoint
    assert current.current_temperature is not None
    assert current.energy_kwh > 0
    matching = [e for e in EventTrace.get_history("classroom_01") if e.payload.get("scenario_id") == scenario_id]
    kinds = {event.event_type for event in matching}
    assert {"SIMULATED_OCCUPANCY_INPUT", "OCCUPANCY_CHANGED", "STATE_EVALUATED",
            "INTELLIGENCE_REQUESTED", "INTELLIGENCE_RESPONSE", "RECOMMENDATION_REJECTED",
            "FALLBACK_ACTIVATED", "RECOMMENDATION_VALIDATED", "CONTROL_COMMAND_REQUESTED",
            "CONTROL_VALIDATION", "CONTROL_COMMAND_SENT", "CONTROL_ACKNOWLEDGED",
            "HVAC_RESPONSE", "ENERGY_UPDATE"} <= kinds
    assert all(event.payload.get("simulation") is True for event in matching)
    activity = scheduler.demo_control_activity["classroom_01"]
    assert activity["occupancy_at_command"] == 35
    assert activity["command_id"] == result.command_id
    assert activity["timestamp"]
    await asyncio.sleep(0.05)
    EventBroadcaster.unsubscribe(receive)
    assert {"SIMULATED_OCCUPANCY_INPUT", "RECOMMENDATION_VALIDATED", "CONTROL_ACKNOWLEDGED",
            "HVAC_RESPONSE", "ENERGY_UPDATE"} <= {event["event_type"] for event in websocket_messages}
  asyncio.run(run())


def test_failed_simulated_control_is_not_reported_as_success():
  async def run():
    EventTrace.clear()
    control, state_service, service, scheduler = _pipeline(failure_mode="acknowledgement")
    zone = "classroom_01"
    # Obtain the deterministic scenario input through the explicit provider.
    source = DemoScenarioOccupancyProvider({zone: state_service._zones[zone].capacity})
    source.set_counts((zone,), (35,))
    occupancy = source.get_occupancy(zone)
    await scheduler.process_simulated_occupancy(zone, occupancy, "scenario-failure-test")
    assert service.last_results[zone].success is False
    assert service.last_results[zone].status == "FAILED"
    assert control.read_control_state(zone).control_state == "FAILED"
    assert any(event.event_type == "DEMO_CONTROL_ACTIVITY" and event.status == "FAILED"
               for event in EventTrace.get_history(zone))
  asyncio.run(run())


def test_demo_monitoring_respects_final_command_limit_rejection(monkeypatch):
  async def run():
    control, state_service, service, scheduler = _pipeline()
    zone = "classroom_01"
    source = DemoScenarioOccupancyProvider({zone: state_service._zones[zone].capacity})
    source.set_counts((zone,), (35,))
    writes = []
    original_write = control.write_command

    def count_write(command):
      writes.append(command)
      return original_write(command)

    control.write_command = count_write
    service.safety.validate_command = lambda command, state: SafetyValidationResult(
        outcome="REJECTED", rejection_reason="Command test limit rejection.",
        source="command_limit_gate",
    )
    await scheduler.process_simulated_occupancy(
        zone, source.get_occupancy(zone), "scenario-command-limit-test",
    )
    result = service.last_results[zone]
    assert result.status == "REJECTED"
    assert result.error_code == "COMMAND_LIMIT_REJECTED"
    assert writes == []

  from backend.intelligence.schemas import SafetyValidationResult
  asyncio.run(run())

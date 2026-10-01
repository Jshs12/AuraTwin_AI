from datetime import timedelta
import asyncio
from types import SimpleNamespace


from backend.core.events import EventTrace
from backend.core.time import utc_now
from backend.schemas.events import OccupancyEvent
from backend.schemas.data_quality import QualityState
from backend.schemas.state import ZoneState
from backend.services.data_quality import DataQualityGate
from backend.services.optimization_intervals import OptimizationIntervalService
from tests.unit.test_simulated_bacnet_control import make_state


def state_with_occupancy(count: int) -> ZoneState:
    state = make_state()
    now = utc_now()
    capacity = state.zone.capacity
    pct = count / capacity * 100
    state.occupancy = OccupancyEvent(zone_id=state.zone.zone_id, people_count=count,
        capacity=capacity, occupancy_percentage=pct,
        occupancy_state="EMPTY" if count == 0 else "LOW" if pct < 30 else "MEDIUM" if pct < 70 else "HIGH",
        timestamp=now, observed_at=now, source="simulated_test", simulated=True)
    state.data_quality = DataQualityGate().assess_zone_state(state)
    return state


def test_identical_occupancy_holds_active_interval_without_repeat_evaluation():
    EventTrace.clear()
    service = OptimizationIntervalService()
    initial = state_with_occupancy(17)
    interval = service.start(initial, 18.0, 22.0)
    proceed, still_active = service.observe(state_with_occupancy(17))
    assert not proceed
    assert still_active.interval_id == interval.interval_id
    assert service.active(initial.zone.zone_id).optimized_setpoint == 22.0
    assert not [event for event in EventTrace._events if event.event_type == "OPTIMIZATION_COMPLETED"]


def test_occupancy_change_completes_interval_with_boundary_timestamps_and_events():
    EventTrace.clear()
    service = OptimizationIntervalService()
    initial = state_with_occupancy(17)
    interval = service.start(initial, 18.0, 22.0)
    proceed, completed = service.observe(state_with_occupancy(11))
    assert proceed
    assert completed.interval_id == interval.interval_id
    assert completed.status == "COMPLETED"
    assert completed.starting_occupancy == 17 and completed.ending_occupancy == 11
    assert completed.ended_at is not None and completed.duration_seconds is not None
    assert service.active(initial.zone.zone_id) is None
    assert service.history(initial.zone.zone_id)[0].interval_id == interval.interval_id
    event_types = [event.event_type for event in EventTrace._events]
    assert "OPTIMIZATION_STARTED" in event_types
    assert "OPTIMIZATION_HOLDING" in event_types
    assert "OPTIMIZATION_COMPLETED" in event_types
    assert "ENERGY_IMPACT_UNAVAILABLE" in event_types


def test_interval_never_fabricates_energy_or_cost_when_valid_boundaries_are_absent():
    service = OptimizationIntervalService()
    initial = state_with_occupancy(4)
    interval = service.start(initial, 21.0, 22.0)
    _, completed = service.observe(state_with_occupancy(6))
    assert completed.interval_id == interval.interval_id
    assert completed.energy_consumed_kwh is None
    assert completed.cost_consumed is None


def test_invalid_occupancy_cannot_close_interval_or_trigger_next_evaluation():
    service = OptimizationIntervalService()
    initial = state_with_occupancy(8)
    interval = service.start(initial, 18.0, 22.0)
    changed = state_with_occupancy(12)
    changed.data_quality.signals["occupancy"].state = QualityState.INVALID
    proceed, still_active = service.observe(changed)
    assert not proceed
    assert still_active.interval_id == interval.interval_id
    assert service.active(initial.zone.zone_id).status == "ACTIVE"


def test_valid_cumulative_energy_and_tariff_produce_consumption_and_cost_not_savings():
    service = OptimizationIntervalService()
    initial = state_with_occupancy(8)
    start_time = utc_now() - timedelta(minutes=30)
    initial.energy = initial.energy.model_copy(update={"energy_kwh": 10.0, "observed_at": start_time,
                                                       "source": "demo_meter", "is_simulated": True})
    initial.tariff = initial.tariff.model_copy(update={"rate_per_kwh": 0.2, "observed_at": start_time,
                                                        "source": "demo_tariff", "simulated": True})
    initial.energy.tariff = initial.tariff
    initial.data_quality = DataQualityGate().assess_zone_state(initial)
    interval = service.start(initial, 18.0, 22.0)

    ending = state_with_occupancy(11)
    end_time = utc_now()
    ending.energy = ending.energy.model_copy(update={"energy_kwh": 10.75, "observed_at": end_time,
                                                      "source": "demo_meter", "is_simulated": True})
    ending.tariff = ending.tariff.model_copy(update={"rate_per_kwh": 0.2, "observed_at": end_time,
                                                     "source": "demo_tariff", "simulated": True})
    ending.energy.tariff = ending.tariff
    ending.data_quality = DataQualityGate().assess_zone_state(ending)

    _, completed = service.observe(ending)
    assert completed.interval_id == interval.interval_id
    assert completed.energy_consumed_kwh == 0.75
    assert completed.cost_consumed == 0.15
    assert completed.simulated is True
    assert not hasattr(completed, "energy_savings_kwh")


def test_intervals_api_preserves_authentication_and_zone_building_scope():
    from tests.security_test_utils import bare_client, make_client

    endpoint = "/api/zones/classroom_01/optimization-intervals"
    assert bare_client().get(endpoint).status_code == 401
    assert make_client(building_ids=set()).get(endpoint).status_code == 403
    response = make_client().get(endpoint)
    assert response.status_code == 200
    assert response.json()["persistence"] == "PROCESS_LOCAL"
    assert "active" in response.json() and "completed" in response.json()


def test_demo_scheduler_holds_setpoint_and_suppresses_duplicate_command_on_same_occupancy():
    from backend.core.monitoring import ZoneMonitoringScheduler

    class Provider:
        def __init__(self):
            self.advances = 0

        def advance_simulation(self, *_args):
            self.advances += 1

    class StateService:
        configuration_repository = None

        def __init__(self):
            self._zones = {"test_zone": state_with_occupancy(8).zone}
            self.control_provider = Provider()

        def get_zone_state(self, zone_id, occupancy_override=None):
            return state_with_occupancy(occupancy_override.people_count if occupancy_override else 8)

    class Workflow:
        def __init__(self):
            self.calls = 0

        def recommend(self, state):
            self.calls += 1
            validation = SimpleNamespace(validated_setpoint=22.0, outcome="VALIDATED")
            return SimpleNamespace(validation=validation, recommendation_kind="deterministic_fallback")

    class Control:
        def __init__(self):
            self.provider_writes = 0

        def apply_validated_recommendation_result(self, *_args, **_kwargs):
            self.provider_writes += 1
            return SimpleNamespace(success=True, applied_setpoint=22.0, previous_setpoint=18.0,
                command_id="sim-command", requested_setpoint=22.0, provider="SIMULATED BACNET",
                timestamp=utc_now(), status="SUCCESS")

    state_service, workflow, control = StateService(), Workflow(), Control()
    scheduler = ZoneMonitoringScheduler(state_service, workflow, control, yolo_provider=None)
    first = state_with_occupancy(8).occupancy
    second = first.model_copy(update={"observed_at": utc_now(), "timestamp": utc_now()})
    async def exercise():
        started = await scheduler.process_simulated_occupancy("test_zone", first, "test-scenario")
        assert started.get("optimization_holding") is None
        assert scheduler.optimization_intervals.active("test_zone") is not None

        held = await scheduler.process_simulated_occupancy("test_zone", second, "test-scenario")
        assert held["optimization_holding"] is True

    asyncio.run(exercise())
    assert workflow.calls == 1
    assert control.provider_writes == 1
    assert state_service.control_provider.advances == 2  # telemetry advances; no second command is issued

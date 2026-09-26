import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

from backend.core.events import MAX_EVENT_HISTORY, EventBroadcaster, EventTrace
from backend.energy.telemetry import BuildingEnergyTelemetry
from backend.hvac.simulator import HVACSimulator
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider
from backend.schemas.control import HVACCommand


def _zone(occupancy: int, hvac_kw: float):
    return SimpleNamespace(
        zone=SimpleNamespace(zone_id="z"),
        occupancy=SimpleNamespace(people_count=occupancy),
        temperature=27.0,
        hvac_status=SimpleNamespace(power_kw=hvac_kw, present_value=24.0, hvac_mode="COOLING"),
    )


def test_building_power_rises_with_occupancy_and_falls_when_occupancy_falls():
    telemetry = BuildingEnergyTelemetry()
    low = telemetry.record({"z": _zone(2, 1.0)})
    high = telemetry.record({"z": _zone(35, 2.0)})
    fall = telemetry.record({"z": _zone(8, 1.1)})
    assert high["power_kw"] > low["power_kw"]
    assert fall["power_kw"] < high["power_kw"]


def test_hvac_occupancy_temperature_and_setpoint_change_causal_load():
    low = HVACSimulator.simulate_occupancy_response("z", 27.0, 24.0, 0, elapsed_hours=1 / 60)
    occupied = HVACSimulator.simulate_occupancy_response("z", 27.0, 24.0, 35, elapsed_hours=1 / 60)
    relaxed = HVACSimulator.simulate_occupancy_response("z", 27.0, 26.0, 35, elapsed_hours=1 / 60)
    assert occupied.power_kw > low.power_kw
    assert relaxed.power_kw < occupied.power_kw
    assert occupied.current_temperature != low.current_temperature


def test_simulated_provider_load_tracks_occupancy_and_setpoint():
    provider = SimulatedBACnetBuildingControlProvider(zone_ids=["z"])
    provider.advance_simulation("z", 35, 1 / 60)
    occupied_power = provider.read_status("z").power_kw
    provider.write_setpoint(HVACCommand(zone_id="z", setpoint=26.0, source="test"))
    provider.advance_simulation("z", 35, 1 / 60)
    assert provider.read_status("z").power_kw < occupied_power
    provider.advance_simulation("z", 0, 1 / 60)
    assert provider.read_status("z").power_kw < occupied_power


def test_metrics_are_calculated_from_the_same_sample_window():
    telemetry = BuildingEnergyTelemetry()
    telemetry.record({"z": _zone(1, 1.25)})
    telemetry.record({"z": _zone(2, 2.25)})
    telemetry.record({"z": _zone(0, 0.5)})
    metrics = telemetry.metrics()
    powers = [sample["power_kw"] for sample in telemetry.samples]
    assert metrics["current_power_kw"] == telemetry.samples[-1]["power_kw"]
    assert metrics["average_power_kw"] == round(sum(powers) / len(powers), 3)
    assert metrics["peak_power_kw"] == max(powers)


def test_known_power_interval_integrates_to_kwh_and_cost():
    telemetry = BuildingEnergyTelemetry(base_load_kw=0.0, expected_zone_updates=1)
    sample = telemetry.record(
        {"z": _zone(0, 10.0)}, elapsed_hours=0.5,
        tariff={"rate_per_kwh": 0.20},
    )
    assert sample["power_kw"] == 10.0
    assert sample["energy_kwh"] == 5.0
    assert sample["cost"] == 1.0


def test_samples_are_time_ordered_and_reset_clears_energy_history():
    telemetry = BuildingEnergyTelemetry()
    for people in (2, 10, 20):
        telemetry.record({"z": _zone(people, 1.5)}, elapsed_hours=0.25)
    samples = list(telemetry.samples)
    assert [sample["timestamp"] for sample in samples] == sorted(sample["timestamp"] for sample in samples)
    assert telemetry.samples[-1]["energy_kwh"] > 0
    telemetry.reset()
    assert list(telemetry.samples) == []
    assert telemetry.metrics() == {"current_power_kw": 0.0, "average_power_kw": 0.0, "peak_power_kw": 0.0}


def test_building_energy_update_is_broadcast_over_existing_websocket_event_path():
    async def run():
        EventTrace.clear()
        messages = []
        async def receive(message):
            messages.append(message)
        EventBroadcaster.subscribe(receive)
        telemetry = BuildingEnergyTelemetry()
        sample = telemetry.record({"z": _zone(18, 2.0)}, elapsed_hours=1 / 60)
        await EventBroadcaster.broadcast(EventTrace._events[-1])
        EventBroadcaster.unsubscribe(receive)
        event = json.loads(messages[-1])
        assert event["event_type"] == "ENERGY_UPDATE"
        assert event["zone_id"] == "building"
        assert event["payload"]["power_kw"] == sample["power_kw"]
        assert event["payload"]["occupancy"] == 18
        assert event["payload"]["zones"]["z"]["hvac_mode"] == "COOLING"
        sample_time = datetime.fromisoformat(sample["timestamp"])
        event_time = datetime.fromisoformat(event["timestamp"])
        assert sample_time.tzinfo is not None
        assert event_time.tzinfo is not None
        assert event_time >= sample_time
    asyncio.run(run())


def test_failed_websocket_subscriber_is_removed_without_blocking_other_clients():
    async def run():
        received = []

        async def disconnected(_message):
            raise RuntimeError("socket is closed")

        async def connected(message):
            received.append(message)

        EventBroadcaster.subscribe(disconnected)
        EventBroadcaster.subscribe(connected)
        try:
            EventTrace.log_event("WS_TEST", "z", "test", {})
            await EventBroadcaster.broadcast(EventTrace._events[-1])
            assert len(received) == 1
            assert disconnected not in EventBroadcaster._subscribers
        finally:
            EventBroadcaster.unsubscribe(disconnected)
            EventBroadcaster.unsubscribe(connected)

    asyncio.run(run())


def test_event_history_enforces_its_configured_bound():
    EventTrace.clear()
    for index in range(MAX_EVENT_HISTORY + 5):
        EventTrace.log_event("BOUND_TEST", "z", "test", {"index": index})
    assert len(EventTrace._events) == MAX_EVENT_HISTORY
    assert EventTrace._events[0].payload["index"] == 5
    assert EventTrace._events[-1].payload["index"] == MAX_EVENT_HISTORY + 4
    EventTrace.clear()

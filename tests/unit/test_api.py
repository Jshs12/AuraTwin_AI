from datetime import datetime, timezone
import time
from fastapi.testclient import TestClient
from backend.api.main import app
from backend.core.events import EventTrace

client = TestClient(app)

def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "AuraTwin AI V2 Backend is running"}

def test_get_zones():
    response = client.get("/api/zones")
    assert response.status_code == 200
    data = response.json()
    assert "zones" in data
    assert len(data["zones"]) == 10

def test_get_zone_not_found():
    response = client.get("/api/zones/invalid_zone")
    assert response.status_code == 404

def test_simulated_control_status_and_semantic_points():
    status = client.get("/api/control/status")
    assert status.status_code == 200
    assert status.json() == {"provider": "SIMULATED BACNET", "simulated": True, "ready": True}
    points = client.get("/api/control/zones/classroom_01/points")
    assert points.status_code == 200
    names = {point["point_name"] for point in points.json()["points"]}
    assert {"temperature_present_value", "occupancy_present_value", "cooling_setpoint", "heating_setpoint"} <= names

    monitoring = client.get("/api/monitoring/status").json()
    occupancy = client.get("/api/occupancy/status").json()
    assert monitoring["occupancy_provider"] == occupancy["provider"]
    assert monitoring["occupancy_provider_ready"] == occupancy["ready"]

def test_control_endpoint_preserves_status_and_returns_structured_result():
    recommendation = client.post("/api/zones/classroom_01/recommendation")
    assert recommendation.status_code == 200
    response = client.post("/api/zones/classroom_01/control", json=recommendation.json())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["success"] is True
    assert data["control_result"]["provider"] == "SIMULATED BACNET"
    assert data["control_result"]["simulated"] is True

def test_demo_api_lifecycle_summary_and_simulation_markers():
    initial = client.get("/api/demo/status")
    assert initial.status_code == 200
    assert initial.json()["simulation"] is True

    started = client.post("/api/demo/start")
    assert started.status_code == 200
    assert started.json()["status"] == "RUNNING"
    scenario_id = started.json()["scenario_id"]
    scenario_task = app.state.demo_scenario._task
    monitor_task = app.state.monitoring_scheduler._task
    duplicate_start = client.post("/api/demo/start")
    assert duplicate_start.status_code == 409
    assert app.state.demo_scenario._task is scenario_task
    assert app.state.monitoring_scheduler._task is monitor_task
    monitoring = client.get("/api/monitoring/status").json()
    assert monitoring["running"] is True
    assert monitoring["demo_simulation"] is True
    assert monitoring["demo_phase"] == "LOW OCCUPANCY"
    assert client.post("/api/monitoring/start").status_code == 409
    current_zone = client.get("/api/zones/classroom_01/state").json()
    assert current_zone["occupancy"]["people_count"] == 2
    assert current_zone["occupancy_source"] == "demo_scenario_simulation"
    # A later historical event must not replace the authoritative zone state.
    EventTrace.log_event("OCCUPANCY_CHANGED", "classroom_01", "test", {"current_count": 999})
    assert client.get("/api/zones/classroom_01/state").json()["occupancy"]["people_count"] == 2
    activities = client.get("/api/demo/activity").json()["activities"]
    expected_occupants = {"classroom_01": 2, "classroom_02": 0, "lab_01": 8, "lab_02": 4}
    assert activities
    for activity in activities:
        payload = activity["payload"]
        assert activity["event_id"] == payload["command_id"]
        assert payload["occupancy_at_command"] == expected_occupants[activity["zone_id"]]
        assert payload["requested_setpoint"] is not None
        assert payload["applied_setpoint"] is not None
        assert payload["status"] in {"SUCCESS", "FAILED"}
        assert payload["timestamp"]
        assert datetime.fromisoformat(payload["timestamp"]).utcoffset().total_seconds() == 0

    paused = client.post("/api/demo/pause")
    assert paused.status_code == 200 and paused.json()["status"] == "PAUSED"
    resumed = client.post("/api/demo/resume")
    assert resumed.status_code == 200 and resumed.json()["status"] == "RUNNING"
    assert client.post("/api/demo/speed", json={"speed_multiplier": 2}).json()["speed_multiplier"] == 2
    assert client.post("/api/demo/speed", json={"speed_multiplier": 3}).status_code == 422

    demo_events = [event for event in EventTrace._events if event.payload.get("scenario_id") == scenario_id]
    kinds = {event.event_type for event in demo_events}
    assert {"DEMO_PHASE_CHANGED", "SIMULATED_OCCUPANCY_INPUT", "INTELLIGENCE_REQUESTED",
            "RECOMMENDATION_VALIDATED"} <= kinds
    event_response = client.get("/api/demo/events")
    assert event_response.status_code == 200
    assert event_response.json()["scenario_id"] == scenario_id
    assert {event["event_type"] for event in event_response.json()["events"]} >= {
        "SIMULATED_OCCUPANCY_INPUT", "INTELLIGENCE_REQUESTED", "RECOMMENDATION_VALIDATED",
    }
    scenario_timestamps = [datetime.fromisoformat(event["timestamp"]) for event in event_response.json()["events"]]
    assert all(stamp.tzinfo is not None and stamp.utcoffset() == timezone.utc.utcoffset(stamp)
               for stamp in scenario_timestamps)
    assert scenario_timestamps == sorted(scenario_timestamps)
    energy_sample_times = [datetime.fromisoformat(sample["timestamp"])
                           for sample in client.get("/api/demo/building-summary").json()["energy_history"]]
    assert energy_sample_times == sorted(energy_sample_times)

    stopped = client.post("/api/demo/stop")
    assert stopped.status_code == 200 and stopped.json()["status"] == "STOPPED"
    summary = client.get("/api/demo/building-summary")
    assert summary.status_code == 200
    assert summary.json()["simulation"] is True
    assert summary.json()["active_zones"] == 4
    assert len(summary.json()["energy_history"]) >= 4
    assert summary.json()["simulated_power_kw"] == summary.json()["energy_history"][-1]["power_kw"]
    assert summary.json()["energy_metrics"]["current_power_kw"] == summary.json()["simulated_power_kw"]
    assert summary.json()["energy_metrics"]["average_power_kw"] > 0
    assert summary.json()["energy_metrics"]["peak_power_kw"] >= summary.json()["energy_metrics"]["average_power_kw"]
    assert summary.json()["energy_history"][-1]["zones"]
    assert summary.json()["energy_model"] == "Deterministic software simulation used to demonstrate the relationship between occupancy, HVAC operation, and energy telemetry."
    assert "simulated_power_kw" in summary.json()
    reset = client.post("/api/demo/reset")
    assert reset.status_code == 200 and reset.json()["status"] == "IDLE"
    assert client.get("/api/demo/status").json()["scenario_id"] is None
    cleared = client.get("/api/demo/building-summary").json()
    assert cleared["energy_history"] == []
    assert cleared["simulated_power_kw"] == 0
    assert cleared["energy_metrics"] == {"current_power_kw": 0.0, "average_power_kw": 0.0, "peak_power_kw": 0.0}
    assert client.get("/api/demo/activity").json()["activities"] == []
    assert client.get("/api/demo/events").json()["events"] == []
    zone_after_reset = client.get("/api/zones/classroom_01/state").json()
    assert zone_after_reset["temperature"] == 27.1
    assert zone_after_reset["hvac_status"]["present_value"] == 24.0
    assert zone_after_reset["energy"]["energy_kwh"] == 0.0


def test_reset_then_start_reproduces_the_same_initial_demo_state():
    snapshots = []
    for _ in range(2):
        reset = client.post("/api/demo/reset")
        assert reset.status_code == 200
        started = client.post("/api/demo/start")
        assert started.status_code == 200
        zones = {}
        for zone_id in ("classroom_01", "classroom_02", "lab_01", "lab_02"):
            state = client.get(f"/api/zones/{zone_id}/state").json()
            zones[zone_id] = {
                "occupancy": state["occupancy"]["people_count"],
                "temperature": state["temperature"],
                "setpoint": state["hvac_status"]["present_value"],
                "hvac_mode": state["hvac_status"]["hvac_mode"],
                "power_kw": state["hvac_status"]["power_kw"],
            }
        snapshots.append(zones)
        assert client.post("/api/demo/reset").status_code == 200
    assert snapshots[0] == snapshots[1]

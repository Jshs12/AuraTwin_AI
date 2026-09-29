import pytest
from fastapi.testclient import TestClient
from backend.api.main import app
from tests.security_test_utils import make_client

client = make_client()

def test_one_zone_loop():
    zone_id = "classroom_01"
    
    # 1. Retrieve State
    res_state = client.get(f"/api/zones/{zone_id}/state")
    assert res_state.status_code == 200
    state_data = res_state.json()
    assert state_data["zone"]["zone_id"] == zone_id
    # people_count depends on the active provider:
    # YOLO: 0 until detect_from_image() has been called; Mock: always 18
    assert state_data["occupancy"]["people_count"] >= 0
    assert state_data["temperature"] == 27.1
    assert state_data["energy"]["power_kw"] == 4.8
    assert state_data["hvac_status"]["present_value"] == 24.0 # default
    
    # 2. Generate Recommendation
    res_rec = client.post(f"/api/zones/{zone_id}/recommendation")
    assert res_rec.status_code == 200
    rec_data = res_rec.json()
    assert rec_data["zone_id"] == zone_id
    assert rec_data["recommendation_kind"] == "intelligence"
    assert rec_data["validation"]["outcome"] == "VALIDATED"
    assert rec_data["validation"]["validated_setpoint"] == 24.0
    assert rec_data["intelligence_recommendation"]["provider"] == "mock_intelligence_provider"
    
    # 3. Apply Control
    res_ctrl = client.post(f"/api/zones/{zone_id}/control", json=rec_data)
    assert res_ctrl.status_code == 200
    assert res_ctrl.json()["status"] == "SUCCESS"
    
    # 4. Verify State Updated
    res_state2 = client.get(f"/api/zones/{zone_id}/state")
    assert res_state2.json()["hvac_status"]["present_value"] == 24.0
    
    # 5. Verify Event Trace
    res_hist = client.get(f"/api/zones/{zone_id}/history")
    history = res_hist.json()["history"]
    assert len(history) > 0
    event_types = [e["event_type"] for e in history]
    assert "OCCUPANCY_DETECTED" in event_types
    assert "STATE_EVALUATED" in event_types
    assert "OPTIMIZATION_REQUESTED" in event_types
    assert "OPTIMIZATION_RECOMMENDATION" in event_types
    assert "CONTROL_COMMAND" in event_types
    assert "HVAC_RESPONSE" in event_types

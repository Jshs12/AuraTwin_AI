from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from typing import Dict, Any

from backend.core.monitoring import ZoneMonitoringScheduler
from backend.core.mock_providers import MockEnergyStreamProvider
from backend.core.events import EventBroadcaster

router = APIRouter(tags=["Monitoring"])

def get_scheduler(request: Request) -> ZoneMonitoringScheduler:
    return request.app.state.monitoring_scheduler

def get_energy_stream(request: Request) -> MockEnergyStreamProvider:
    return request.app.state.energy_stream_provider

@router.get("/status")
def get_monitoring_status(request: Request) -> dict:
    status = get_scheduler(request).get_status()
    status["occupancy_provider"] = getattr(request.app.state, "occupancy_provider_mode", "unknown")
    status["occupancy_provider_ready"] = getattr(request.app.state, "occupancy_provider_ready", False)
    scenario = getattr(request.app.state, "demo_scenario", None)
    status["demo_simulation"] = bool(scenario and scenario.scenario_id)
    status["demo_phase"] = scenario.status_payload()["current_phase"] if scenario else None
    if status["demo_simulation"]:
        for zone in status["zones"]:
            zone["occupancy_source"] = "DEMO"
            zone["phase"] = status["demo_phase"]
    else:
        for zone in status["zones"]:
            zone["occupancy_source"] = "YOLO" if status["camera_provider"].lower() == "yolo" else status["camera_provider"].upper()
            zone["phase"] = None
    return status

@router.post("/start")
async def start_monitoring(request: Request) -> dict:
    scheduler = get_scheduler(request)
    scenario = getattr(request.app.state, "demo_scenario", None)
    if scenario and scenario.status.value in {"RUNNING", "PAUSED"}:
        raise HTTPException(status_code=409, detail="Use Demo Mode controls while the scenario is active.")
    await scheduler.stop_and_wait()
    if scenario and scenario.scenario_id:
        await scenario.reset()
        scheduler.demo_mode = False
        scheduler.demo_current_states.clear()
        scheduler.demo_control_activity.clear()
        scheduler.energy_telemetry.reset()
        provider = scheduler.state_service.control_provider
        if provider is not None and hasattr(provider, "reset_simulation"):
            provider.reset_simulation()
    scheduler.start()
    get_energy_stream(request).start()
    return {"status": "started"}

@router.post("/stop")
async def stop_monitoring(request: Request) -> dict:
    scheduler = get_scheduler(request)
    scenario = getattr(request.app.state, "demo_scenario", None)
    if scenario and scenario.status.value in {"RUNNING", "PAUSED"}:
        await scenario.stop()
        scheduler.demo_mode = False
    await scheduler.stop_and_wait()
    await get_energy_stream(request).stop_and_wait()
    return {"status": "stopped"}

@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await websocket.accept()
    
    async def send_message(message: str):
        await websocket.send_text(message)
        
    EventBroadcaster.subscribe(send_message)
    try:
        while True:
            # Keep connection open
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        EventBroadcaster.unsubscribe(send_message)

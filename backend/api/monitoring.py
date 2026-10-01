from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from typing import Dict, Any
import asyncio
import json

from backend.core.monitoring import ZoneMonitoringScheduler
from backend.core.mock_providers import MockEnergyStreamProvider
from backend.core.events import EventBroadcaster
from backend.security.dependencies import get_current_user, require_permission, require_any_permission
from backend.security.roles import Permission, Role, has_permission

router = APIRouter(tags=["Monitoring"])

def get_scheduler(request: Request) -> ZoneMonitoringScheduler:
    return request.app.state.monitoring_scheduler

def get_energy_stream(request: Request) -> MockEnergyStreamProvider:
    return request.app.state.energy_stream_provider

@router.get("/status")
def get_monitoring_status(request: Request, user=Depends(require_any_permission(Permission.SYSTEM_READ, Permission.BUILDING_READ))) -> dict:
    scheduler = get_scheduler(request)
    status = scheduler.get_status()
    allowed_zone_ids = {zone.zone_id for zone in request.app.state.configuration_repository.zones_for_user(user)}
    if user.role == Role.OPERATOR and not allowed_zone_ids:
        raise HTTPException(status_code=403, detail="Building access denied")
    status["zones"] = [zone for zone in status["zones"] if zone["zone_id"] in allowed_zone_ids]
    if user.role == Role.OPERATOR:
        status["running"] = bool(status["running"] and any(
            zone_id in allowed_zone_ids for zone_id in scheduler.monitored_zones))
        status["zones_enabled"] = len([zone for zone in scheduler.monitored_zones if zone in allowed_zone_ids])
        status["zones_total"] = len(allowed_zone_ids)
    status["occupancy_provider"] = getattr(request.app.state, "occupancy_provider_mode", "unknown")
    status["occupancy_provider_ready"] = getattr(request.app.state, "occupancy_provider_ready", False)
    scenario = getattr(request.app.state, "demo_scenario", None)
    demo_zone_ids = set(getattr(scenario, "ZONES", ()))
    demo_visible = user.role == Role.ADMIN or demo_zone_ids.issubset(allowed_zone_ids)
    demo_active = bool(demo_visible and scheduler.demo_mode and scenario
                       and scenario.status.value in {"RUNNING", "PAUSED"})
    status["demo_simulation"] = demo_active
    status["demo_phase"] = scenario.status_payload()["current_phase"] if demo_active else None
    status["monitoring_scope"] = ("DEMO_SCENARIO" if demo_active else
        "CONFIGURED_BUILDING" if status["running"] else "STOPPED")
    status["configured_zones_total"] = len(allowed_zone_ids)
    status["demo_zones_total"] = len(demo_zone_ids.intersection(allowed_zone_ids))
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
async def start_monitoring(request: Request, user=Depends(require_permission(Permission.MONITORING_MANAGE))) -> dict:
    scheduler = get_scheduler(request)
    if scheduler._running and scheduler.scope_owner_id not in (None, user.user_id):
        raise HTTPException(status_code=409, detail="Monitoring is active in another authorized building scope.")
    zone_ids = [zone.zone_id for zone in request.app.state.configuration_repository.zones_for_user(user)]
    if not zone_ids:
        raise HTTPException(status_code=409, detail="No active zones are configured for an assigned building.")
    scenario = getattr(request.app.state, "demo_scenario", None)
    if scenario and scenario.status.value in {"RUNNING", "PAUSED"}:
        raise HTTPException(status_code=409, detail="Use Demo Mode controls while the scenario is active.")
    await scheduler.stop_and_wait()
    scheduler.configure_zones(zone_ids)
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
    scheduler.scope_owner_id = user.user_id
    get_energy_stream(request).start()
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="monitoring_start", resource="monitoring", success=True)
    return {"status": "started"}

@router.post("/stop")
async def stop_monitoring(request: Request, user=Depends(require_permission(Permission.MONITORING_MANAGE))) -> dict:
    scheduler = get_scheduler(request)
    if scheduler._running and scheduler.scope_owner_id not in (None, user.user_id):
        raise HTTPException(status_code=403, detail="Monitoring is active in another authorized building scope.")
    scenario = getattr(request.app.state, "demo_scenario", None)
    if scenario and scenario.status.value in {"RUNNING", "PAUSED"}:
        await scenario.stop()
        scheduler.demo_mode = False
    await scheduler.stop_and_wait()
    scheduler.scope_owner_id = None
    await get_energy_stream(request).stop_and_wait()
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="monitoring_stop", resource="monitoring", success=True)
    return {"status": "stopped"}

@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    token = websocket.query_params.get("access_token")
    if not token:
        await websocket.close(code=4401)
        return
    async def current_access():
        try:
            from backend.security.jwt import decode_access_token
            claims = decode_access_token(token)
            current_user = websocket.app.state.auth_service.users.get_by_id(claims["sub"])
            if current_user is None or not current_user.active or current_user.role.value != claims["role"]:
                return None, set(), 4401
            required = Permission.EVENTS_READ if current_user.role == Role.ADMIN else Permission.MONITORING_MANAGE
            if not has_permission(current_user.role, required):
                return None, set(), 4403
            zones = {zone.zone_id for zone in websocket.app.state.configuration_repository.zones_for_user(current_user)}
            if current_user.role == Role.OPERATOR and not zones:
                return None, set(), 4403
            return current_user, zones, None
        except Exception:
            return None, set(), 4401

    user, allowed_zone_ids, auth_close_code = await current_access()
    if auth_close_code is not None:
        await websocket.close(code=auth_close_code)
        return
    await websocket.accept()

    async def send_message(message: str):
        try:
            event = json.loads(message)
            current_user, current_zones, close_code = await current_access()
            if close_code is not None:
                await websocket.close(code=close_code)
                raise RuntimeError("WebSocket authorization expired")
            # Building-wide/unknown events are never exposed to scoped operators.
            if current_user.role == Role.OPERATOR and event.get("zone_id") not in current_zones:
                return
            await websocket.send_text(message)
        except RuntimeError:
            raise

    EventBroadcaster.subscribe(send_message)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=15)
            except asyncio.TimeoutError:
                # Recheck JWT expiry, account status and current building scope
                # even when the event stream is idle.
                _, _, close_code = await current_access()
                if close_code is not None:
                    await websocket.close(code=close_code)
                    break
    except WebSocketDisconnect:
        pass
    finally:
        EventBroadcaster.unsubscribe(send_message)

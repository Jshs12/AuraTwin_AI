import os
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from backend.core.paths import PROJECT_ROOT, resolve_project_path

# ---------------------------------------------------------------------------
# Load .env file early, before any os.environ.get() calls.
# Supports both python-dotenv (preferred) and a simple fallback parser.
# ---------------------------------------------------------------------------
def _load_dotenv(env_path: str = ".env"):
    p = resolve_project_path(env_path)
    if not p.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(str(p), override=False)
    except ImportError:
        # Manual fallback: parse KEY=VALUE lines, skip comments, don't override
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)

_load_dotenv()

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.schemas.state import ZoneState
from backend.schemas.optimization import OptimizationRecommendation
from backend.schemas.control import ControlModeUpdate
from backend.intelligence.schemas import RecommendationSubmission, IntelligenceRecommendation
from backend.intelligence.service import RecommendationWorkflow
from backend.intelligence.factory import intelligence_provider_from_environment
from backend.core.mock_providers import (
    MockOccupancyProvider, MockTemperatureProvider,
    MockTariffProvider, MockEnergyProvider,
)
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider
from backend.services.zone_state import ZoneStateService
from backend.optimization.engine import OptimizationEngine
from backend.services.control import ControlService
from backend.services.control_state import ZoneControlStateService
from backend.core.events import EventTrace, EventBroadcaster
from backend.core.monitoring import ZoneMonitoringScheduler
from backend.core.mock_providers import MockEnergyStreamProvider
from backend.demo.scenario import DemoScenarioEngine
from backend.api import monitoring
import asyncio
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from backend.security.service import AuthService
from backend.security.audit import AuditService
from backend.security.roles import Permission, Role
from backend.security.repository import BuildingAccessRepository
from backend.security.schemas import LoginRequest, OperatorCreateRequest, TokenResponse, UserResponse, LogoutResponse
from backend.security.models import User
from backend.security.passwords import hash_password
from backend.security.dependencies import get_current_user, require_permission, require_any_permission, require_zone_permission, require_zone_any_permission
from backend.security.jwt import token_settings
from backend.database.config import DatabaseSettings
from backend.database.runtime import initialize_local_demo_database
from backend.database.repositories import SQLAlchemyUserRepository, SQLAlchemyOrganizationRepository
from backend.database.configuration import SQLAlchemyConfigurationRepository
from backend.telemetry.repository import SQLAlchemyTelemetryRepository
from backend.telemetry.service import TelemetryPersistenceService
from datetime import datetime
from backend.api.configuration import router as configuration_router

# Configuration/auth records are persistent. Live ZoneState, telemetry and events
# remain runtime-only. SQLite is the local development default; Postgres requires
# an explicit migration before this application can use it.
database_engine, database_sessions = initialize_local_demo_database(DatabaseSettings.from_environment())
configuration_repository = SQLAlchemyConfigurationRepository(database_sessions)
organization_repository = SQLAlchemyOrganizationRepository(database_sessions)
telemetry_service = TelemetryPersistenceService(
    SQLAlchemyTelemetryRepository(database_sessions), configuration_repository,
)

@asynccontextmanager
async def lifespan(application: FastAPI):
    try:
        yield
    finally:
        scheduler = getattr(application.state, "monitoring_scheduler", None)
        energy_stream = getattr(application.state, "energy_stream_provider", None)
        pending_tasks = [
            task for task in (
                getattr(scheduler, "_task", None),
                getattr(energy_stream, "task", None),
            ) if task is not None
        ]
        if scheduler is not None:
            scheduler.stop()
        if energy_stream is not None:
            energy_stream.stop()
        scenario = getattr(application.state, "demo_scenario", None)
        if scenario is not None:
            await scenario.reset()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        database_engine.dispose()


app = FastAPI(title="AuraTwin AI V2 API", lifespan=lifespan)

app.state.auth_service = AuthService(SQLAlchemyUserRepository(database_sessions))
app.state.audit_service = AuditService()
app.state.configuration_repository = configuration_repository
app.state.organization_repository = organization_repository
app.state.building_access = BuildingAccessRepository(configuration_repository)

def _cors_origins() -> list[str]:
    configured = os.getenv("AURATWIN_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")
    origins = [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip() and origin.strip() != "*"]
    return origins or ["http://127.0.0.1:5173", "http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve annotated YOLO detection images
_detections_dir = resolve_project_path("data/detections")
_detections_dir.mkdir(parents=True, exist_ok=True)
class AuthenticatedDetectionFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        from starlette.requests import Request as StarletteRequest
        from backend.security.jwt import decode_access_token
        request = StarletteRequest(scope)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        try:
            claims = decode_access_token(auth.split(" ", 1)[1])
            user = app.state.auth_service.users.get_by_id(claims["sub"])
            if user is None or not user.active or user.role.value != claims["role"]:
                raise ValueError("invalid user")
            if user.role == Role.OPERATOR:
                filename = Path(path).name
                if filename != path:
                    raise HTTPException(404, "Detection file not found")
                candidates = [zone for zone in app.state.configuration_repository.zones_for_user(user)
                              if filename.startswith(zone.zone_id + "_")]
                if not candidates:
                    raise HTTPException(403, "Building access denied")
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except Exception:
            return JSONResponse({"detail": "Invalid or expired authentication"}, status_code=401)
        return await super().get_response(path, scope)


app.mount("/api/static", AuthenticatedDetectionFiles(directory=str(_detections_dir)), name="static")

# ---------------------------------------------------------------------------
# Provider instantiation — dynamic based on OCCUPANCY_PROVIDER env var
# ---------------------------------------------------------------------------
OCCUPANCY_PROVIDER_MODE = os.environ.get("OCCUPANCY_PROVIDER", "mock").lower()

tariff_prov = MockTariffProvider()
temp_prov = MockTemperatureProvider()
energy_prov = MockEnergyProvider(tariff_prov)
_configured_zone_ids = [item.zone_id for item in configuration_repository.runtime_zone_configs()]
CONTROL_PROVIDER_MODE = os.environ.get("BUILDING_CONTROL_PROVIDER", "simulated_bacnet").strip().lower()
if CONTROL_PROVIDER_MODE not in {"simulated_bacnet", "mock"}:
    print("[WARNING] Unsupported BUILDING_CONTROL_PROVIDER; using simulated_bacnet.")
    CONTROL_PROVIDER_MODE = "simulated_bacnet"
control_prov = SimulatedBACnetBuildingControlProvider(zone_ids=_configured_zone_ids)

yolo_provider = None  # Will be set only if mode == "yolo"
OCCUPANCY_PROVIDER_ERROR = None

if OCCUPANCY_PROVIDER_MODE == "yolo":
    try:
        from backend.core.yolo_provider import YOLOOccupancyProvider

        yolo_provider = YOLOOccupancyProvider(
            model_path=os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt"),
            confidence=float(os.environ.get("YOLO_CONFIDENCE", "0.40")),
            device=os.environ.get("YOLO_DEVICE", "auto"),
        )
        occ_prov = yolo_provider
    except Exception as e:
        OCCUPANCY_PROVIDER_ERROR = f"YOLO provider initialization failed ({type(e).__name__})."
        print(f"[ERROR] {OCCUPANCY_PROVIDER_ERROR}")
        occ_prov = None
else:
    occ_prov = MockOccupancyProvider()

app.state.occupancy_provider_mode = OCCUPANCY_PROVIDER_MODE
app.state.occupancy_provider_ready = (
    occ_prov is not None
    and (not hasattr(occ_prov, "is_ready") or bool(occ_prov.is_ready()))
)

# Instantiate services
zone_state_service = ZoneStateService(occ_prov, temp_prov, energy_prov, control_prov,
                                      configuration_repository=configuration_repository,
                                      telemetry_service=telemetry_service)
control_service = ControlService(control_prov, state_provider=zone_state_service.get_zone_state,
                                 control_states=ZoneControlStateService(initially_enabled=False))
optimizer = OptimizationEngine()
recommendation_workflow = RecommendationWorkflow(provider=intelligence_provider_from_environment())

app.state.energy_stream_provider = MockEnergyStreamProvider(tariff_prov, EventBroadcaster)
# Pass occ_prov (which has detect_from_image now)
app.state.monitoring_scheduler = ZoneMonitoringScheduler(zone_state_service, recommendation_workflow, control_service, occ_prov)
app.state.demo_scenario = DemoScenarioEngine()
app.state.zone_state_service = zone_state_service
app.state.telemetry_service = telemetry_service

app.include_router(monitoring.router, prefix="/api/monitoring")
app.include_router(configuration_router, prefix="/api")


def _public_user(request: Request, user: User):
    payload = request.app.state.auth_service.safe_user(user)
    public_building_ids = []
    for identifier in user.building_ids:
        building = request.app.state.configuration_repository.get_building(identifier)
        public_building_ids.append(building["building_key"] if building else identifier)
    payload["building_ids"] = sorted(public_building_ids)
    return payload


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    result = request.app.state.auth_service.authenticate(body.email, body.password)
    if result is None:
        request.app.state.audit_service.record(action="login_failed", resource="auth", success=False)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token, expires, user = result
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="login_succeeded", resource="auth", success=True)
    return {"access_token": token, "expires_in": expires,
            "user": _public_user(request, user)}


@app.post("/api/auth/logout", response_model=LogoutResponse)
async def logout(user=Depends(get_current_user)):
    return LogoutResponse()


@app.get("/api/auth/me", response_model=UserResponse)
async def auth_me(request: Request, user=Depends(get_current_user)):
    return _public_user(request, user)


@app.get("/api/auth/access")
async def auth_access(request: Request, user=Depends(require_any_permission(Permission.ACCESS_READ, Permission.ACCESS_MANAGE))):
    users = request.app.state.auth_service.users.list_users()
    if user.role == Role.OPERATOR:
        assigned = set(user.building_ids)
        users = [item for item in users if item.role == Role.OPERATOR and assigned.intersection(item.building_ids)]
    return {"users": [{**_public_user(request, item), "active": item.active}
                       for item in users]}


@app.post("/api/auth/operators", status_code=201)
async def create_operator(body: OperatorCreateRequest, request: Request,
                          user=Depends(require_permission(Permission.ACCESS_MANAGE))):
    building_ids = set(body.building_ids or user.building_ids)
    if not building_ids:
        raise HTTPException(422, "Assign at least one building to the operator")
    for building_id in building_ids:
        try:
            from backend.security.dependencies import require_building_access
            require_building_access(building_id, user, request)
        except HTTPException:
            raise HTTPException(403, "Cannot assign an operator to a building outside your access") from None
    created = User(str(uuid.uuid4()), body.email, hash_password(body.password), Role.OPERATOR,
                   True, frozenset(building_ids))
    try:
        request.app.state.auth_service.users.add(created)
    except ValueError:
        raise HTTPException(409, "Account already exists") from None
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Account already exists") from None
        raise
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="operator_created", resource="user", resource_id=created.user_id,
        building_id=next(iter(building_ids)), success=True,
        metadata={"building_ids": sorted(building_ids)})
    return _public_user(request, created)


@app.delete("/api/auth/operators/{user_id}")
async def revoke_operator(user_id: str, request: Request,
                          user=Depends(require_permission(Permission.ACCESS_MANAGE))):
    target = request.app.state.auth_service.users.get_by_id(user_id)
    if target is None or target.role != Role.OPERATOR:
        raise HTTPException(404, "Operator not found")
    if user.role == Role.OPERATOR and not set(user.building_ids).intersection(target.building_ids):
        raise HTTPException(404, "Operator not found")
    revoked = request.app.state.auth_service.users.deactivate(user_id)
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="operator_access_revoked", resource="user", resource_id=user_id,
        building_id=next(iter(target.building_ids), None), success=True)
    return {"user_id": revoked.user_id, "active": revoked.active}


@app.get("/api/audit")
async def get_audit_records(request: Request, _user=Depends(require_permission(Permission.AUDIT_READ))):
    return {"records": [
        {"timestamp": row.timestamp.isoformat(), "user_id": row.user_id, "role": row.role,
         "action": row.action, "resource": row.resource, "resource_id": row.resource_id,
         "building_id": row.building_id, "success": row.success, "metadata": row.metadata}
        for row in request.app.state.audit_service.list_records()
    ]}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_zones():
    return [zone.api_dict() for zone in configuration_repository.runtime_zone_configs()]


def _zone_building_id(zone_id: str) -> str:
    zone = configuration_repository.resolve_zone(zone_id)
    return zone.building_id if zone else ""


def get_zone_capacity(zone_id: str) -> int:
    config = configuration_repository.resolve_zone(zone_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")
    return config.capacity


# ---------------------------------------------------------------------------
# Foundation endpoints (unchanged from Phase 2)
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "AuraTwin AI V2 Backend is running"}


@app.get("/api/demo/status")
async def get_demo_status(request: Request, _user=Depends(require_any_permission(Permission.SYSTEM_READ, Permission.BUILDING_READ))):
    _require_demo_scope(_user, request)
    return app.state.demo_scenario.status_payload()


def _demo_capacities():
    return {zone_id: zone_state_service._zones[zone_id].capacity
            for zone_id in app.state.demo_scenario.ZONES if zone_id in zone_state_service._zones}


def _require_demo_scope(user, request: Request):
    if user.role == Role.ADMIN:
        return
    application = getattr(request, "app", request)
    allowed = {zone.zone_id for zone in application.state.configuration_repository.zones_for_user(user)}
    if not set(application.state.demo_scenario.ZONES).issubset(allowed):
        raise HTTPException(status_code=403, detail="Demo scenario is outside the assigned building scope.")


@app.post("/api/demo/start")
async def start_demo(user=Depends(require_permission(Permission.MONITORING_MANAGE))):
    try:
        scheduler = app.state.monitoring_scheduler
        demo_zone_ids = list(app.state.demo_scenario.ZONES)
        _require_demo_scope(user, app)
        if scheduler._running and scheduler.scope_owner_id not in (None, user.user_id):
            raise HTTPException(status_code=409, detail="Monitoring is active in another authorized building scope.")
        scenario_status = app.state.demo_scenario.status.value
        if scenario_status in {"RUNNING", "PAUSED"}:
            detail = ("Demo scenario is already running." if scenario_status == "RUNNING"
                      else "Demo scenario is paused; use resume.")
            raise HTTPException(status_code=409, detail=detail)
        if app.state.demo_scenario.status.value not in {"RUNNING", "PAUSED"}:
            await app.state.demo_scenario.reset()
            if hasattr(control_prov, "reset_simulation"):
                control_prov.reset_simulation()
            control_service.last_results.clear()
            scheduler.demo_current_states.clear()
            scheduler.demo_control_activity.clear()
            scheduler.energy_telemetry.reset()
            for zone in scheduler.zone_states.values():
                zone.update({"last_snapshot": None, "last_inference": None, "last_frame": None,
                             "last_people_count": 0, "status": "IDLE"})
        await scheduler.stop_and_wait()
        scheduler.configure_zones(demo_zone_ids)
        scheduler.demo_mode = True
        scheduler.scope_owner_id = user.user_id
        scheduler.start()
        await app.state.energy_stream_provider.stop_and_wait()

        async def on_demo_complete():
            scheduler.demo_mode = False
            await scheduler.stop_and_wait()
            scheduler.scope_owner_id = None
            scheduler.configure_zones(zone.zone_id for zone in configuration_repository.zones_for_user(user))

        result = await app.state.demo_scenario.start(
            scheduler.process_simulated_occupancy,
            _demo_capacities(),
            on_complete=on_demo_complete,
        )
        app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
            action="demo_start", resource="demo", resource_id=result.get("scenario_id"),
            success=True, metadata={"simulation": True})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/demo/pause")
async def pause_demo(request: Request, user=Depends(require_permission(Permission.MONITORING_MANAGE))):
    _require_demo_scope(user, request)
    try:
        result = await app.state.demo_scenario.pause()
        app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
            action="demo_pause", resource="demo", success=True, metadata={"simulation": True})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/demo/resume")
async def resume_demo(request: Request, user=Depends(require_permission(Permission.MONITORING_MANAGE))):
    _require_demo_scope(user, request)
    try:
        result = await app.state.demo_scenario.resume()
        app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
            action="demo_resume", resource="demo", success=True, metadata={"simulation": True})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/demo/stop")
async def stop_demo(request: Request, user=Depends(require_permission(Permission.MONITORING_MANAGE))):
    _require_demo_scope(user, request)
    try:
        result = await app.state.demo_scenario.stop()
        app.state.monitoring_scheduler.demo_mode = False
        await app.state.monitoring_scheduler.stop_and_wait()
        app.state.monitoring_scheduler.scope_owner_id = None
        app.state.monitoring_scheduler.configure_zones(
            zone.zone_id for zone in configuration_repository.zones_for_user(user))
        app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
            action="demo_stop", resource="demo", success=True, metadata={"simulation": True})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/demo/reset")
async def reset_demo(request: Request, user=Depends(require_permission(Permission.MONITORING_MANAGE))):
    _require_demo_scope(user, request)
    result = await app.state.demo_scenario.reset()
    app.state.monitoring_scheduler.demo_mode = False
    await app.state.monitoring_scheduler.stop_and_wait()
    app.state.monitoring_scheduler.scope_owner_id = None
    await app.state.energy_stream_provider.stop_and_wait()
    if hasattr(control_prov, "reset_simulation"):
        control_prov.reset_simulation()
    control_service.last_results.clear()
    app.state.monitoring_scheduler.demo_current_states.clear()
    app.state.monitoring_scheduler.demo_control_activity.clear()
    app.state.monitoring_scheduler.energy_telemetry.reset()
    for zone in app.state.monitoring_scheduler.zone_states.values():
        zone.update({"last_snapshot": None, "last_inference": None, "last_frame": None,
                    "last_people_count": 0, "status": "IDLE"})
    app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="demo_reset", resource="demo", success=True, metadata={"simulation": True})
    return result


@app.post("/api/demo/speed")
async def set_demo_speed(body: dict, request: Request, user=Depends(require_permission(Permission.MONITORING_MANAGE))):
    _require_demo_scope(user, request)
    try:
        return await app.state.demo_scenario.set_speed(float(body.get("speed_multiplier")))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/demo/building-summary")
async def get_demo_building_summary(request: Request, _user=Depends(require_any_permission(Permission.ENERGY_READ, Permission.BUILDING_READ))):
    _require_demo_scope(_user, request)
    scheduler = app.state.monitoring_scheduler
    current_states = list(scheduler.demo_current_states.values())
    occupancy = [state.occupancy.people_count for state in current_states]
    states = [state.hvac_status for state in current_states]
    count = len(states)
    latest = scheduler.energy_telemetry.samples[-1] if scheduler.energy_telemetry.samples else None
    total_energy = float(latest["energy_kwh"]) if latest else 0.0
    rate = tariff_prov.get_current_tariff().rate_per_kwh
    scenario_id = app.state.demo_scenario.scenario_id
    energy_history = list(scheduler.energy_telemetry.samples)
    return {
        "simulation": True,
        "scenario_id": scenario_id,
        "active_zones": len(app.state.demo_scenario.ZONES),
        "occupied_zones": sum(1 for value in occupancy if value > 0),
        "total_occupants": sum(occupancy),
        "simulated_power_kw": float(latest["power_kw"]) if latest else 0.0,
        "simulated_energy_kwh": round(total_energy, 3),
        "simulated_cost": round(total_energy * rate, 2),
        "energy_metrics": scheduler.energy_telemetry.metrics(),
        "currency": tariff_prov.get_current_tariff().currency,
        "zones_under_active_control": sum(1 for state in states if state.control_state == "APPLIED"),
        "zones_requiring_attention": sum(1 for state in states if state.control_state == "FAILED"),
        "average_zone_temperature": round(sum(state.current_temperature or 0 for state in states) / count, 2) if count else None,
        "average_setpoint": round(sum(float(state.present_value) for state in states) / count, 2) if count else None,
        "provider": control_prov.provider_identity,
        "energy_history": energy_history,
        "energy_model": "Deterministic software simulation used to demonstrate the relationship between occupancy, HVAC operation, and energy telemetry.",
    }


@app.get("/api/demo/activity")
async def get_demo_activity(request: Request, _user=Depends(require_any_permission(Permission.EVENTS_READ, Permission.BUILDING_READ))):
    _require_demo_scope(_user, request)
    scenario_id = app.state.demo_scenario.scenario_id
    activities = []
    if scenario_id:
        for zone_id, payload in app.state.monitoring_scheduler.demo_control_activity.items():
            activities.append({
                "event_id": payload["command_id"], "event_type": "DEMO_CONTROL_ACTIVITY",
                "zone_id": zone_id, "timestamp": payload["timestamp"],
                "source": "demo_scenario", "payload": payload,
                "status": payload["status"],
            })
    return {"scenario_id": scenario_id, "activities": activities[-8:]}


@app.get("/api/demo/events")
async def get_demo_events(request: Request, _user=Depends(require_any_permission(Permission.EVENTS_READ, Permission.MONITORING_MANAGE))):
    _require_demo_scope(_user, request)
    scenario_id = app.state.demo_scenario.scenario_id
    events = [event.model_dump(mode="json") for event in EventTrace._events
              if scenario_id and event.payload.get("scenario_id") == scenario_id]
    return {"scenario_id": scenario_id, "events": events}


@app.get("/api/zones")
async def get_zones(request: Request, building_id: str | None = None,
                    _user=Depends(require_permission(Permission.ZONES_READ))):
    if building_id:
        from backend.security.dependencies import require_building_access
        require_building_access(building_id, _user, request)
        zones = configuration_repository.list_zones(building_id=building_id)
    else:
        zones = configuration_repository.zones_for_user(_user)
    if _user.role == Role.OPERATOR and not zones:
        raise HTTPException(status_code=403, detail="Building access denied")
    return {"zones": [zone.api_dict() for zone in zones]}


@app.get("/api/zones/{zone_id}")
async def get_zone(zone_id: str, _user=Depends(require_zone_permission(Permission.ZONES_READ))):
    zone = configuration_repository.get_zone(zone_id)
    if zone is not None:
        return zone
    raise HTTPException(status_code=404, detail="Zone not found")


def _telemetry_range(start_at: datetime | None, end_at: datetime | None):
    for value in (start_at, end_at):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(status_code=422, detail="Telemetry time filters must include a timezone.")
    if start_at is not None and end_at is not None and start_at > end_at:
        raise HTTPException(status_code=422, detail="start_at must not be after end_at.")


def _telemetry_payload(rows):
    return {"observations": [row.model_dump(mode="json") for row in rows]}


@app.get("/api/buildings/{building_id}/telemetry")
async def get_building_telemetry(building_id: str, request: Request,
        start_at: datetime | None = None, end_at: datetime | None = None,
        limit: int = 500, user=Depends(require_permission(Permission.ZONES_READ))):
    _telemetry_range(start_at, end_at)
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 1000.")
    building = configuration_repository.get_building(building_id)
    if building is None:
        raise HTTPException(status_code=404, detail="Building not found")
    from backend.security.dependencies import require_building_access
    require_building_access(building["building_id"], user, request)
    return _telemetry_payload(telemetry_service.list_building(
        organization_id=building["organization_id"], building_id=building["building_id"],
        start_at=start_at, end_at=end_at, limit=limit))


@app.get("/api/zones/{zone_id}/telemetry")
async def get_zone_telemetry(zone_id: str, start_at: datetime | None = None,
        end_at: datetime | None = None, limit: int = 500,
        _user=Depends(require_zone_permission(Permission.ZONES_READ))):
    _telemetry_range(start_at, end_at)
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 1000.")
    scope = configuration_repository.telemetry_scope(zone_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return _telemetry_payload(telemetry_service.list_zone(
        organization_id=scope["organization_id"], building_id=scope["building_id"],
        zone_id=scope["database_zone_id"], start_at=start_at, end_at=end_at, limit=limit))


@app.get("/api/zones/{zone_id}/telemetry/latest")
async def get_latest_zone_telemetry(zone_id: str,
        _user=Depends(require_zone_permission(Permission.ZONES_READ))):
    scope = configuration_repository.telemetry_scope(zone_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return _telemetry_payload(telemetry_service.list_zone(
        organization_id=scope["organization_id"], building_id=scope["building_id"],
        zone_id=scope["database_zone_id"], limit=1))


@app.get("/api/zones/{zone_id}/state")
async def get_zone_state(zone_id: str, _user=Depends(require_zone_permission(Permission.ZONES_READ))):
    try:
        demo_state = app.state.monitoring_scheduler.demo_current_states.get(zone_id)
        if demo_state is not None:
            state = demo_state
        else:
            state = zone_state_service.get_zone_state(zone_id)
        response = state.model_dump(mode="json")
        response["control_mode"] = control_service.control_states.snapshot(zone_id).model_dump(mode="json")
        return response
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/zones/{zone_id}/control-state")
async def get_zone_control_state(zone_id: str, _user=Depends(require_zone_permission(Permission.ZONES_READ))):
    try:
        zone_state_service.get_zone_state(zone_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return control_service.control_states.snapshot(zone_id)


@app.post("/api/zones/{zone_id}/manual-override")
async def set_manual_override(zone_id: str, body: ControlModeUpdate, request: Request,
                              user=Depends(require_zone_permission(Permission.CONTROL_EXECUTE))):
    if zone_id not in zone_state_service._zones:
        raise HTTPException(status_code=404, detail="Zone not found")
    state = control_service.control_states.set_manual_override(
        zone_id, body.enabled, user_id=user.user_id,
    )
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="manual_override_enabled" if body.enabled else "manual_override_disabled",
        resource="zone_control_state", resource_id=zone_id,
        building_id=_zone_building_id(zone_id), success=True,
        metadata={"enabled": body.enabled},
    )
    return state


@app.post("/api/zones/{zone_id}/control-enabled")
async def set_zone_control_enabled(zone_id: str, body: ControlModeUpdate, request: Request,
                                   user=Depends(require_zone_permission(Permission.CONTROL_EXECUTE))):
    if zone_id not in zone_state_service._zones:
        raise HTTPException(status_code=404, detail="Zone not found")
    if not body.enabled:
        state = control_service.control_states.set_control_enabled(
            zone_id, False, user_id=user.user_id,
        )
        request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
            action="control_disabled", resource="zone_control_state", resource_id=zone_id,
            building_id=_zone_building_id(zone_id), success=True, metadata={"enabled": False},
        )
        return state

    try:
        current = zone_state_service.get_zone_state(zone_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    quality = control_service.data_quality.assess_zone_state(current)
    current.data_quality = quality
    failures = control_service.data_quality.critical_failures(quality)
    if failures:
        request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
            action="control_enable_rejected", resource="zone_control_state", resource_id=zone_id,
            building_id=_zone_building_id(zone_id), success=False,
            metadata={"reason_code": "DATA_QUALITY_REJECTED"},
        )
        raise HTTPException(status_code=409, detail="Fresh critical zone data is required before enabling control.")
    if not control_service.safety.command_policy_ready:
        request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
            action="control_enable_rejected", resource="zone_control_state", resource_id=zone_id,
            building_id=_zone_building_id(zone_id), success=False,
            metadata={"reason_code": "COMMAND_POLICY_UNAVAILABLE"},
        )
        raise HTTPException(status_code=409, detail="Configured command limits are required before enabling control.")
    if not control_service._provider_ready():
        control_service.control_states.note_provider_failure(zone_id,
            command_id="operator-enable", reason_code="PROVIDER_UNAVAILABLE", provider_ready=False)
        request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
            action="control_enable_rejected", resource="zone_control_state", resource_id=zone_id,
            building_id=_zone_building_id(zone_id), success=False,
            metadata={"reason_code": "PROVIDER_UNAVAILABLE"},
        )
        raise HTTPException(status_code=409, detail="Control provider must be ready before enabling control.")
    control_service.control_states.note_provider_ready(zone_id)
    state = control_service.control_states.set_control_enabled(zone_id, True, user_id=user.user_id)
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="control_enabled", resource="zone_control_state", resource_id=zone_id,
        building_id=_zone_building_id(zone_id), success=True,
        metadata={"enabled": True, "fresh_state_checked": True},
    )
    return state


@app.post("/api/zones/{zone_id}/recommendation")
async def generate_recommendation(zone_id: str, _user=Depends(require_zone_permission(Permission.RECOMMENDATIONS_READ))):
    try:
        EventTrace.log_event("OPTIMIZATION_REQUESTED", zone_id, "api", {})
        state = zone_state_service.get_zone_state(zone_id)
        decision = recommendation_workflow.recommend(state)
        EventTrace.log_event(
            "OPTIMIZATION_RECOMMENDATION",
            zone_id,
            "recommendation_workflow",
            {"recommended_setpoint": decision.validation.validated_setpoint,
             "validation_outcome": decision.validation.outcome,
             "recommendation_kind": decision.recommendation_kind},
        )
        return decision
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/zones/{zone_id}/control")
async def apply_control(zone_id: str, decision: RecommendationSubmission,
                        request: Request, user=Depends(require_zone_permission(Permission.CONTROL_EXECUTE))):
    from backend.security.dependencies import zone_building_id
    building_id = zone_building_id(request, zone_id)
    audit = request.app.state.audit_service
    if decision.zone_id != zone_id:
        raise HTTPException(status_code=400, detail="Zone mismatch")
    try:
        state = zone_state_service.get_zone_state(zone_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Rebuild and validate the submitted advisory against fresh state. Client-supplied
    # validation outcomes and setpoints are never trusted.
    if decision.recommendation_kind == "deterministic_fallback" and decision.deterministic_recommendation:
        try:
            det = OptimizationRecommendation.model_validate(decision.deterministic_recommendation)
            candidate = IntelligenceRecommendation(
                zone_id=det.zone_id, recommended_setpoint=det.recommended_setpoint,
                rationale=det.reason, confidence=1.0, provider="deterministic_optimizer",
                model_source=det.source, timestamp=det.timestamp,
                context_reference={"zone_id": zone_id},
            )
        except Exception:
            candidate = None
    else:
        candidate = decision.intelligence_recommendation
    checked = recommendation_workflow.validate_submitted(state, candidate)
    result = control_service.apply_validated_recommendation_result(checked.validation, state)
    audit.record(user_id=user.user_id, role=user.role.value, action="control_execute",
                 resource="zone", resource_id=zone_id, building_id=building_id,
                 success=result.success, metadata={"simulated": bool(result.simulated)})
    return {
        "status": result.status,
        "success": result.success,
        "control_result": result.model_dump(mode="json"),
    }


@app.get("/api/control/status")
async def get_control_status(_user=Depends(require_any_permission(Permission.SYSTEM_READ, Permission.BUILDING_READ))):
    return {
        "provider": control_prov.provider_identity,
        "simulated": control_prov.is_simulated,
        "ready": control_prov.is_ready,
    }


@app.get("/api/control/zones/{zone_id}/points")
async def get_control_points(zone_id: str, _user=Depends(require_zone_permission(Permission.ZONES_READ))):
    if zone_id not in zone_state_service._zones:
        raise HTTPException(status_code=404, detail="Zone not found")
    return {
        "zone_id": zone_id,
        "provider": control_prov.provider_identity,
        "points": [point.model_dump(mode="json") for point in control_prov.get_points(zone_id)],
    }


@app.get("/api/control/zones/{zone_id}/result")
async def get_control_result(zone_id: str, _user=Depends(require_zone_permission(Permission.ZONES_READ))):
    if zone_id not in zone_state_service._zones:
        raise HTTPException(status_code=404, detail="Zone not found")
    result = control_service.last_results.get(zone_id)
    return {"zone_id": zone_id, "result": result.model_dump(mode="json") if result else None}


@app.get("/api/zones/{zone_id}/history")
async def get_zone_history(zone_id: str, _user=Depends(require_zone_any_permission(Permission.EVENTS_READ, Permission.RECOMMENDATIONS_READ))):
    history = EventTrace.get_history(zone_id)
    return {"history": history}


# ---------------------------------------------------------------------------
# Phase 3: YOLO Occupancy Detection endpoint
# ---------------------------------------------------------------------------
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@app.post("/api/occupancy/detect")
async def detect_occupancy(
    request: Request,
    file: UploadFile = File(...),
    zone_id: str = Form(default="classroom_01"),
    _user=Depends(require_permission(Permission.MONITORING_MANAGE)),
):
    """
    Accept an image upload and run person detection via the shared occupancy provider.

    When OCCUPANCY_PROVIDER=yolo, runs real YOLO inference.
    When OCCUPANCY_PROVIDER=mock, uses the mock provider's detect_from_image().
    Both paths use the same shared provider instance that autonomous monitoring uses.
    """
    # Validate file extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {ALLOWED_EXTENSIONS}",
        )

    # Validate zone exists
    capacity = get_zone_capacity(zone_id)
    from backend.security.dependencies import require_building_access
    require_building_access(_zone_building_id(zone_id), _user, request)

    # Guard: if yolo mode was requested but model failed to load, report truthfully.
    if OCCUPANCY_PROVIDER_MODE == "yolo" and (yolo_provider is None or not yolo_provider.is_ready()):
        diagnostic = "Inspect server diagnostics for the model initialization failure."
        raise HTTPException(
            status_code=503,
            detail=f"YOLO provider is not ready. {diagnostic}",
        )

    # Read image bytes
    image_bytes = await file.read()
    if len(image_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Use the shared occ_prov instance — the same one autonomous monitoring uses.
    # This guarantees a single provider/model instance regardless of mode.
    try:
        event, metadata = occ_prov.detect_from_image(image_bytes, zone_id, capacity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid image format. Upload a valid JPG, PNG, or WEBP file.") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail="Occupancy inference failed. Check server diagnostics.") from None

    # Log trace
    EventTrace.log_event(
        "OCCUPANCY_DETECTED",
        zone_id,
        "occupancy_provider",
        {"people_count": event.people_count},
    )

    return {
        "occupancy": event,
        "detection": metadata,
    }


@app.get("/api/occupancy/status")
async def occupancy_status(_user=Depends(require_any_permission(Permission.SYSTEM_READ, Permission.BUILDING_READ))):
    """Report which occupancy provider is active and whether it is ready."""
    if OCCUPANCY_PROVIDER_MODE == "yolo":
        ready = yolo_provider is not None and yolo_provider.is_ready()
        return {
            "provider": "yolo",
            "ready": ready,
            "model": Path(os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt")).name,
            "resolved_model_path": (
                Path(yolo_provider.model_path).relative_to(PROJECT_ROOT).as_posix()
                if yolo_provider is not None and Path(yolo_provider.model_path).is_relative_to(PROJECT_ROOT)
                else (Path(yolo_provider.model_path).name if yolo_provider is not None else None)
            ),
            "confidence_threshold": float(os.environ.get("YOLO_CONFIDENCE", "0.40")),
            "device": os.environ.get("YOLO_DEVICE", "auto"),
            "diagnostic": (f"Model initialization failed ({yolo_provider.load_error.split(':', 1)[0]})."
                           if yolo_provider is not None and yolo_provider.load_error else
                           "YOLO provider is not initialized." if yolo_provider is None else None),
        }
    return {"provider": "mock", "ready": True}


# ---------------------------------------------------------------------------
# Placeholders for future phases
# ---------------------------------------------------------------------------
@app.post("/api/telemetry")
async def post_telemetry(data: dict, _user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    return {"status": "NOT_IMPLEMENTED"}


@app.get("/api/bacnet/status")
async def get_bacnet_status(_user=Depends(require_permission(Permission.SYSTEM_READ))):
    return {"status": "NOT_IMPLEMENTED"}


@app.get("/api/lyzr/status")
async def get_lyzr_status(_user=Depends(require_permission(Permission.SYSTEM_READ))):
    return {"status": "NOT_IMPLEMENTED"}


@app.get("/api/n8n/status")
async def get_n8n_status(_user=Depends(require_permission(Permission.SYSTEM_READ))):
    return {"status": "NOT_IMPLEMENTED"}

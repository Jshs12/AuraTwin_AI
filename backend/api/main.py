import os
import json
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
from backend.core.events import EventTrace, EventBroadcaster
from backend.core.monitoring import ZoneMonitoringScheduler
from backend.core.mock_providers import MockEnergyStreamProvider
from backend.demo.scenario import DemoScenarioEngine
from backend.api import monitoring
import asyncio

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


app = FastAPI(title="AuraTwin AI V2 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve annotated YOLO detection images
_detections_dir = resolve_project_path("data/detections")
_detections_dir.mkdir(parents=True, exist_ok=True)
app.mount("/api/static", StaticFiles(directory=str(_detections_dir)), name="static")

# ---------------------------------------------------------------------------
# Provider instantiation — dynamic based on OCCUPANCY_PROVIDER env var
# ---------------------------------------------------------------------------
OCCUPANCY_PROVIDER_MODE = os.environ.get("OCCUPANCY_PROVIDER", "mock").lower()

tariff_prov = MockTariffProvider()
temp_prov = MockTemperatureProvider()
energy_prov = MockEnergyProvider(tariff_prov)
_zone_config_path = resolve_project_path("data/building/zones.json")
_configured_zone_ids = []
if _zone_config_path.exists():
    with open(_zone_config_path, "r", encoding="utf-8") as _zone_file:
        _configured_zone_ids = [entry["zone_id"] for entry in json.load(_zone_file)]
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
zone_state_service = ZoneStateService(occ_prov, temp_prov, energy_prov, control_prov)
control_service = ControlService(control_prov)
optimizer = OptimizationEngine()
recommendation_workflow = RecommendationWorkflow(provider=intelligence_provider_from_environment())

app.state.energy_stream_provider = MockEnergyStreamProvider(tariff_prov, EventBroadcaster)
# Pass occ_prov (which has detect_from_image now)
app.state.monitoring_scheduler = ZoneMonitoringScheduler(zone_state_service, recommendation_workflow, control_service, occ_prov)
app.state.demo_scenario = DemoScenarioEngine()

app.include_router(monitoring.router, prefix="/api/monitoring")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_zones():
    path = resolve_project_path("data/building/zones.json")
    if not path.exists():
        return []
    with open(path, "r") as f:
        return json.load(f)


def get_zone_capacity(zone_id: str) -> int:
    """Look up a zone's capacity from zones.json."""
    for z in load_zones():
        if z["zone_id"] == zone_id:
            return z.get("capacity", 1)
    raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")


# ---------------------------------------------------------------------------
# Foundation endpoints (unchanged from Phase 2)
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "AuraTwin AI V2 Backend is running"}


@app.get("/api/demo/status")
async def get_demo_status():
    return app.state.demo_scenario.status_payload()


def _demo_capacities():
    return {zone_id: zone_state_service._zones[zone_id].capacity
            for zone_id in app.state.demo_scenario.ZONES if zone_id in zone_state_service._zones}


@app.post("/api/demo/start")
async def start_demo():
    try:
        scheduler = app.state.monitoring_scheduler
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
        scheduler.demo_mode = True
        scheduler.start()
        await app.state.energy_stream_provider.stop_and_wait()

        async def on_demo_complete():
            scheduler.demo_mode = False
            await scheduler.stop_and_wait()

        return await app.state.demo_scenario.start(
            scheduler.process_simulated_occupancy,
            _demo_capacities(),
            on_complete=on_demo_complete,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/demo/pause")
async def pause_demo():
    try:
        return await app.state.demo_scenario.pause()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/demo/resume")
async def resume_demo():
    try:
        return await app.state.demo_scenario.resume()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/demo/stop")
async def stop_demo():
    try:
        result = await app.state.demo_scenario.stop()
        app.state.monitoring_scheduler.demo_mode = False
        await app.state.monitoring_scheduler.stop_and_wait()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/demo/reset")
async def reset_demo():
    result = await app.state.demo_scenario.reset()
    app.state.monitoring_scheduler.demo_mode = False
    await app.state.monitoring_scheduler.stop_and_wait()
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
    return result


@app.post("/api/demo/speed")
async def set_demo_speed(body: dict):
    try:
        return await app.state.demo_scenario.set_speed(float(body.get("speed_multiplier")))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/demo/building-summary")
async def get_demo_building_summary():
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
        "active_zones": len(app.state.monitoring_scheduler.monitored_zones),
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
async def get_demo_activity():
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
async def get_demo_events():
    scenario_id = app.state.demo_scenario.scenario_id
    events = [event.model_dump(mode="json") for event in EventTrace._events
              if scenario_id and event.payload.get("scenario_id") == scenario_id]
    return {"scenario_id": scenario_id, "events": events}


@app.get("/api/zones")
async def get_zones():
    return {"zones": load_zones()}


@app.get("/api/zones/{zone_id}")
async def get_zone(zone_id: str):
    zones = load_zones()
    for z in zones:
        if z["zone_id"] == zone_id:
            return z
    raise HTTPException(status_code=404, detail="Zone not found")


@app.get("/api/zones/{zone_id}/state")
async def get_zone_state(zone_id: str):
    try:
        demo_state = app.state.monitoring_scheduler.demo_current_states.get(zone_id)
        if demo_state is not None:
            return demo_state
        state = zone_state_service.get_zone_state(zone_id)
        return state
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/zones/{zone_id}/recommendation")
async def generate_recommendation(zone_id: str):
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
async def apply_control(zone_id: str, decision: RecommendationSubmission):
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
    return {
        "status": result.status,
        "success": result.success,
        "control_result": result.model_dump(mode="json"),
    }


@app.get("/api/control/status")
async def get_control_status():
    return {
        "provider": control_prov.provider_identity,
        "simulated": control_prov.is_simulated,
        "ready": control_prov.is_ready,
    }


@app.get("/api/control/zones/{zone_id}/points")
async def get_control_points(zone_id: str):
    if zone_id not in zone_state_service._zones:
        raise HTTPException(status_code=404, detail="Zone not found")
    return {
        "zone_id": zone_id,
        "provider": control_prov.provider_identity,
        "points": [point.model_dump(mode="json") for point in control_prov.get_points(zone_id)],
    }


@app.get("/api/control/zones/{zone_id}/result")
async def get_control_result(zone_id: str):
    if zone_id not in zone_state_service._zones:
        raise HTTPException(status_code=404, detail="Zone not found")
    result = control_service.last_results.get(zone_id)
    return {"zone_id": zone_id, "result": result.model_dump(mode="json") if result else None}


@app.get("/api/zones/{zone_id}/history")
async def get_zone_history(zone_id: str):
    history = EventTrace.get_history(zone_id)
    return {"history": history}


# ---------------------------------------------------------------------------
# Phase 3: YOLO Occupancy Detection endpoint
# ---------------------------------------------------------------------------
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@app.post("/api/occupancy/detect")
async def detect_occupancy(
    file: UploadFile = File(...),
    zone_id: str = Form(default="classroom_01"),
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
async def occupancy_status():
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
async def post_telemetry(data: dict):
    return {"status": "NOT_IMPLEMENTED"}


@app.get("/api/bacnet/status")
async def get_bacnet_status():
    return {"status": "NOT_IMPLEMENTED"}


@app.get("/api/lyzr/status")
async def get_lyzr_status():
    return {"status": "NOT_IMPLEMENTED"}


@app.get("/api/n8n/status")
async def get_n8n_status():
    return {"status": "NOT_IMPLEMENTED"}

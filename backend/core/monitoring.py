import asyncio
import cv2
import math
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional

from backend.core.camera import MockCameraProvider, RTSPCameraProvider
from backend.core.yolo_provider import YOLOOccupancyProvider
from backend.core.events import EventTrace
from backend.services.zone_state import ZoneStateService
from backend.services.control import ControlService
from backend.intelligence.service import RecommendationWorkflow
from backend.schemas.events import OccupancyEvent
from backend.energy.telemetry import BuildingEnergyTelemetry
from backend.core.time import utc_now
import os


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(max(int(os.getenv(name, str(default))), minimum), maximum)
    except (TypeError, ValueError):
        return default


def _threshold_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if math.isfinite(value) and value >= 0 else default
    except (TypeError, ValueError):
        return default

class SceneChangeDetector:
    def __init__(self, threshold: float = 50.0):
        self.threshold = threshold

    def detect(self, img_bytes1: bytes, img_bytes2: bytes) -> tuple[bool, float]:
        """Returns (scene_changed, change_score)"""
        if not img_bytes1 or not img_bytes2:
            return True, 100.0

        nparr1 = np.frombuffer(img_bytes1, np.uint8)
        nparr2 = np.frombuffer(img_bytes2, np.uint8)
        
        img1 = cv2.imdecode(nparr1, cv2.IMREAD_GRAYSCALE)
        img2 = cv2.imdecode(nparr2, cv2.IMREAD_GRAYSCALE)

        if img1 is None or img2 is None:
            return True, 100.0

        img1 = cv2.resize(img1, (64, 64))
        img2 = cv2.resize(img2, (64, 64))

        err = np.sum((img1.astype("float") - img2.astype("float")) ** 2)
        err /= float(img1.shape[0] * img1.shape[1])

        return bool(err > self.threshold), float(err)


class ZoneMonitoringScheduler:
    def __init__(self, state_service: ZoneStateService, workflow: RecommendationWorkflow, control_service: ControlService, yolo_provider: YOLOOccupancyProvider, energy_telemetry: BuildingEnergyTelemetry | None = None):
        self.state_service = state_service
        self.workflow = workflow
        self.control_service = control_service
        self.yolo_provider = yolo_provider

        # Config
        self.snapshot_interval = _bounded_int_env("SNAPSHOT_INTERVAL_SECONDS", 5, 1, 300)
        self.inference_cooldown = _bounded_int_env("INFERENCE_COOLDOWN_SECONDS", 30, 0, 3600)
        camera_type = os.getenv("CAMERA_PROVIDER", "mock").strip().lower()
        if camera_type == "rtsp":
            self.camera = RTSPCameraProvider()
        elif camera_type == "mock":
            self.camera = MockCameraProvider()
        else:
            print("[ZoneMonitoringScheduler] Unsupported camera provider; using mock.")
            camera_type = "mock"
            self.camera = MockCameraProvider()
        self.camera_provider = camera_type

        self.scene_detector = SceneChangeDetector(threshold=_threshold_env("SCENE_CHANGE_THRESHOLD", 50.0))

        # Initially support specific zones
        self.monitored_zones = ["classroom_01", "classroom_02", "lab_01", "lab_02"]
        
        self.zone_states: Dict[str, Dict[str, Any]] = {
            z: {
                "last_snapshot": None,
                "last_inference": None,
                "last_frame": None,
                "last_people_count": 0,
                "status": "IDLE"
            } for z in self.monitored_zones
        }

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.demo_mode = False
        self.demo_current_states: dict[str, Any] = {}
        self.demo_control_activity: dict[str, dict] = {}
        self.energy_telemetry = energy_telemetry or BuildingEnergyTelemetry()

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitoring_loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def stop_and_wait(self):
        task = self._task
        self.stop()
        if task is not None and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass

    def get_status(self) -> dict:
        now = utc_now()
        zones_info = []
        for z, s in self.zone_states.items():
            cooldown_rem = 0
            if s["last_inference"]:
                elapsed = (now - s["last_inference"]).total_seconds()
                cooldown_rem = max(0, self.inference_cooldown - elapsed)
                
            zones_info.append({
                "zone_id": z,
                "status": s["status"],
                "last_snapshot": s["last_snapshot"].isoformat() if s["last_snapshot"] else None,
                "last_inference": s["last_inference"].isoformat() if s["last_inference"] else None,
                "cooldown_remaining_seconds": round(cooldown_rem, 1),
                "last_people_count": s["last_people_count"]
            })

        return {
            "running": self._running,
            "zones_enabled": len(self.monitored_zones),
            "zones_total": 10, # hardcoded per requirements
            "camera_provider": self.camera_provider,
            "demo_simulation": self.demo_mode,
            "snapshot_interval_seconds": self.snapshot_interval,
            "zones": zones_info
        }

    async def _monitoring_loop(self):
        index = 0
        while self._running:
            if self.demo_mode:
                await asyncio.sleep(self.snapshot_interval)
                continue
            if not self.monitored_zones:
                await asyncio.sleep(self.snapshot_interval)
                continue

            zone_id = self.monitored_zones[index]
            index = (index + 1) % len(self.monitored_zones)

            await self._process_zone(zone_id)
            await asyncio.sleep(self.snapshot_interval)

    async def _process_zone(self, zone_id: str):
        state = self.zone_states[zone_id]
        state["status"] = "CAPTURING"
        
        # 1. Snapshot
        loop = asyncio.get_running_loop()
        # Camera adapters may perform blocking network or file I/O. Keep the
        # FastAPI event loop responsive while a frame capture times out/fails.
        frame = await loop.run_in_executor(None, self.camera.get_frame, zone_id)
        now = utc_now()
        state["last_snapshot"] = now
        
        if not frame:
            state["status"] = "ERROR: NO FRAME"
            return

        EventTrace.log_event("SNAPSHOT_CAPTURED", zone_id, "monitoring", {"size": len(frame)})

        # 2. Scene Change Detection
        state["status"] = "DETECTING SCENE CHANGE"
        changed, score = True, 100.0
        if state["last_frame"]:
            changed, score = self.scene_detector.detect(state["last_frame"], frame)
        
        state["last_frame"] = frame

        if not changed:
            state["status"] = "MONITORING (NO CHANGE)"
            return

        EventTrace.log_event("SCENE_CHANGED", zone_id, "monitoring", {"score": round(score, 2)})

        # 3. Cooldown Check
        if state["last_inference"]:
            elapsed = (now - state["last_inference"]).total_seconds()
            if elapsed < self.inference_cooldown:
                state["status"] = "MONITORING (COOLDOWN)"
                return

        # 4. YOLO Inference
        state["status"] = "RUNNING YOLO"
        try:
            zone_obj = self.state_service._zones[zone_id]
            # Offload blocking YOLO call to thread pool
            occ_event, meta = await loop.run_in_executor(
                None, 
                self.yolo_provider.detect_from_image, 
                frame, 
                zone_id, 
                zone_obj.capacity
            )
            state["last_inference"] = now
            
            # 5. Occupancy Delta Check
            prev_count = state["last_people_count"]
            curr_count = occ_event.people_count
            delta = curr_count - prev_count
            state["last_people_count"] = curr_count
            
            if delta != 0:
                EventTrace.log_event("OCCUPANCY_CHANGED", zone_id, "monitoring", {
                    "previous_count": prev_count,
                    "current_count": curr_count,
                    "delta": delta
                })
            
            # Note: EventTrace.log_event("YOLO_DETECTION",...) is already called inside YOLOOccupancyProvider.detect_from_image

            # 6. Zone State Pipeline (Full Loop)
            # The detect_from_image method already caches the result for state_service
            zstate = self.state_service.get_zone_state(zone_id)
            
            # 7. Optimization
            decision = self.workflow.recommend(zstate)
            EventTrace.log_event("OPTIMIZATION_RECOMMENDATION", zone_id, "monitoring", {
                "recommended_setpoint": decision.validation.validated_setpoint,
                "current_setpoint": zstate.hvac_status.present_value,
                "validation_outcome": decision.validation.outcome,
            })
            
            # 8. Control Service
            if (decision.validation.outcome in {"VALIDATED", "FALLBACK"}
                    and decision.validation.validated_setpoint != zstate.hvac_status.present_value):
                self.control_service.apply_validated_recommendation(decision.validation, zstate)

        except Exception as e:
            print(f"[ZoneMonitoringScheduler] Error processing {zone_id} ({type(e).__name__}).")
            state["status"] = "ERROR"
            return

        state["status"] = "MONITORING"

    async def process_simulated_occupancy(self, zone_id: str, occupancy: OccupancyEvent, scenario_id: str, elapsed_hours: float = 0.0):
        """Feed an explicit demo input into the same state/recommendation/safety/control path."""
        if zone_id not in self.state_service._zones:
            raise ValueError(f"Zone {zone_id} not found")
        with EventTrace.context(scenario_id=scenario_id, simulation=True):
            status = self.zone_states.setdefault(zone_id, {"last_snapshot": None, "last_inference": None,
                "last_frame": None, "last_people_count": 0, "status": "IDLE"})
            previous = status["last_people_count"]
            status["last_people_count"] = occupancy.people_count
            status["last_inference"] = utc_now()
            status["status"] = "DEMO SIMULATION"
            EventTrace.log_event("SIMULATED_OCCUPANCY_INPUT", zone_id, "demo_scenario_occupancy_provider",
                                 {"people_count": occupancy.people_count,
                                  "occupancy_state": occupancy.occupancy_state,
                                  "provider_source": "demo_scenario_simulation"})
            if previous != occupancy.people_count:
                EventTrace.log_event("OCCUPANCY_CHANGED", zone_id, "demo_scenario_occupancy_provider",
                                     {"previous_count": previous, "current_count": occupancy.people_count,
                                      "delta": occupancy.people_count - previous})
            state = self.state_service.get_zone_state(zone_id, occupancy_override=occupancy)
            decision = self.workflow.recommend(state)
            EventTrace.log_event("OPTIMIZATION_RECOMMENDATION", zone_id, "monitoring_scheduler",
                                 {"recommended_setpoint": decision.validation.validated_setpoint,
                                  "current_setpoint": state.hvac_status.present_value,
                                  "validation_outcome": decision.validation.outcome,
                                  "recommendation_kind": decision.recommendation_kind})
            if (decision.validation.outcome in {"VALIDATED", "FALLBACK"}
                    and decision.validation.validated_setpoint != state.hvac_status.present_value):
                control_state = self.state_service.get_zone_state(
                    zone_id, occupancy_override=occupancy)
                result = self.control_service.apply_validated_recommendation_result(
                    decision.validation, state, current_state=control_state,
                    final_state_provider=lambda current_zone: self.state_service.get_zone_state(
                        current_zone, occupancy_override=occupancy))
                activity = {
                    "command_id": result.command_id,
                    "occupancy_at_command": occupancy.people_count,
                    "previous_setpoint": result.previous_setpoint,
                    "requested_setpoint": result.requested_setpoint,
                    "applied_setpoint": result.applied_setpoint,
                    "success": result.success,
                    "provider": result.provider,
                    "timestamp": result.timestamp.isoformat(),
                    "status": result.status,
                }
                self.demo_control_activity[zone_id] = activity
                EventTrace.log_event("DEMO_CONTROL_ACTIVITY", zone_id, "demo_scenario", activity,
                                     status="SUCCESS" if result.success else "FAILED")
            advance = getattr(self.state_service.control_provider, "advance_simulation", None)
            if advance:
                advance(zone_id, occupancy.people_count, elapsed_hours)
            state = self.state_service.get_zone_state(zone_id, occupancy_override=occupancy)
            self.demo_current_states[zone_id] = state
            self.energy_telemetry.record(
                self.demo_current_states, elapsed_hours,
                tariff={"rate_per_kwh": state.tariff.rate_per_kwh},
            )
            return {"zone_id": zone_id, "decision": decision, "occupancy": occupancy}

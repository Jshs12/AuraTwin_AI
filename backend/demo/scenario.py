from __future__ import annotations

import asyncio
import inspect
import math
import os
from datetime import datetime
from enum import Enum
from typing import Awaitable, Callable
from uuid import uuid4

from backend.core.events import EventTrace
from backend.core.interfaces import OccupancyProvider
from backend.schemas.events import OccupancyEvent
from backend.core.time import utc_now


class ScenarioStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class DemoScenarioOccupancyProvider(OccupancyProvider):
    """Explicit simulated occupancy input; never invokes camera or YOLO inference."""

    def __init__(self, capacities: dict[str, int] | None = None):
        self.capacities = capacities or {}
        self.counts: dict[str, int] = {}

    def set_counts(self, zone_ids: tuple[str, ...], counts: tuple[int, ...]):
        self.counts = dict(zip(zone_ids, counts))

    def get_occupancy(self, zone_id: str) -> OccupancyEvent:
        if zone_id not in self.capacities or zone_id not in self.counts:
            raise ValueError(f"No simulated occupancy input configured for {zone_id}.")
        count = self.counts[zone_id]
        capacity = self.capacities[zone_id]
        pct = min(100.0, count / max(capacity, 1) * 100.0)
        level = "EMPTY" if count == 0 else "LOW" if pct < 30 else "MEDIUM" if pct < 70 else "HIGH"
        observed = utc_now()
        return OccupancyEvent(zone_id=zone_id, people_count=count, capacity=capacity,
                              occupancy_percentage=round(pct, 1), occupancy_state=level,
                              timestamp=observed, observed_at=observed,
                              source="demo_scenario_simulation", simulated=True)


class DemoScenarioEngine:
    """Clock/state engine. Emits only deterministic occupancy simulation inputs."""

    PHASES = (
        ("LOW OCCUPANCY", (2, 0, 8, 4)),
        ("OCCUPANCY RISE", (18, 25, 15, 14)),
        ("HIGH OCCUPANCY", (35, 40, 20, 20)),
        ("OCCUPANCY FALL", (8, 10, 10, 12)),
    )
    ZONES = ("classroom_01", "classroom_02", "lab_01", "lab_02")
    SPEEDS = (0.5, 1.0, 2.0, 5.0)

    def __init__(self, phase_duration_seconds: float | None = None):
        configured_duration = phase_duration_seconds if phase_duration_seconds is not None else os.getenv("DEMO_PHASE_DURATION_SECONDS", "20")
        try:
            parsed_duration = float(configured_duration)
        except (TypeError, ValueError):
            parsed_duration = 20.0
        self.phase_duration_seconds = parsed_duration if math.isfinite(parsed_duration) and parsed_duration > 0 else 20.0
        self.status = ScenarioStatus.IDLE
        self.speed_multiplier = 1.0
        self.scenario_id: str | None = None
        self.started_at: datetime | None = None
        self.elapsed_seconds = 0.0
        self.phase_elapsed_seconds = 0.0
        self.phase_number = 0
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._on_phase: Callable[[str, OccupancyEvent, str, float], Awaitable[None]] | None = None
        self._on_complete: Callable[[], Awaitable[None] | None] | None = None
        self._capacities: dict[str, int] = {}
        self.occupancy_provider = DemoScenarioOccupancyProvider()
        self.last_error: str | None = None

    def status_payload(self) -> dict:
        phase = self.PHASES[self.phase_number - 1][0] if self.phase_number else "NOT STARTED"
        return {
            "scenario_id": self.scenario_id,
            "scenario_name": "Building Occupancy Response",
            "status": self.status.value,
            "current_phase": phase,
            "phase": phase,
            "phase_number": self.phase_number,
            "total_phases": len(self.PHASES),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "phase_elapsed_seconds": round(self.phase_elapsed_seconds, 1),
            "phase_duration_seconds": self.phase_duration_seconds,
            "speed_multiplier": self.speed_multiplier,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "simulation": True,
            "error": self.last_error,
        }

    async def _emit_phase(self):
        if not self.scenario_id or not self._on_phase or self.phase_number < 1:
            return
        name, counts = self.PHASES[self.phase_number - 1]
        self.occupancy_provider.set_counts(self.ZONES, counts)
        with EventTrace.context(scenario_id=self.scenario_id, simulation=True):
            EventTrace.log_event("DEMO_PHASE_CHANGED", "building", "demo_scenario_engine",
                                 {"phase": name, "phase_number": self.phase_number,
                                  "total_phases": len(self.PHASES)})
            for zone_id in self.ZONES:
                occupancy = self.occupancy_provider.get_occupancy(zone_id)
                try:
                    # One phase's simulated HVAC duration tracks its wall-clock
                    # playback duration. Faster playback therefore does not
                    # pretend that more elapsed time occurred in the building.
                    elapsed_hours = (self.phase_duration_seconds / 3600.0
                                     / self.speed_multiplier if self.phase_number > 1 else 0.0)
                    await self._on_phase(zone_id, occupancy, self.scenario_id, elapsed_hours)
                except Exception as exc:
                    self.last_error = f"Scenario input processing failed ({type(exc).__name__})."
                    EventTrace.log_event("DEMO_INPUT_FAILED", zone_id, "demo_scenario_engine",
                                         {"error": self.last_error}, status="FAILED")
                    self.status = ScenarioStatus.STOPPED
                    return

    async def start(
        self,
        on_phase: Callable[[str, OccupancyEvent, str, float], Awaitable[None]],
        capacities: dict[str, int],
        on_complete: Callable[[], Awaitable[None] | None] | None = None,
    ):
        async with self._lock:
            if self.status == ScenarioStatus.RUNNING:
                raise ValueError("Demo scenario is already running.")
            if self.status == ScenarioStatus.PAUSED:
                raise ValueError("Demo scenario is paused; use resume.")
            self._on_phase = on_phase
            self._on_complete = on_complete
            missing = set(self.ZONES) - set(capacities)
            if missing:
                raise ValueError(f"Configured demo zones are missing: {', '.join(sorted(missing))}")
            self._capacities = capacities
            self.occupancy_provider.capacities = dict(capacities)
            self.scenario_id = str(uuid4())
            self.started_at = utc_now()
            self.elapsed_seconds = self.phase_elapsed_seconds = 0.0
            self.last_error = None
            self.phase_number = 1
            self.status = ScenarioStatus.RUNNING
            await self._emit_phase()
            if self.status == ScenarioStatus.RUNNING:
                self._task = asyncio.create_task(self._run())
            return self.status_payload()

    async def _run(self):
        while self.status == ScenarioStatus.RUNNING:
            await asyncio.sleep(0.25)
            if self.status != ScenarioStatus.RUNNING:
                continue
            increment = 0.25 * self.speed_multiplier
            self.elapsed_seconds += increment
            self.phase_elapsed_seconds += increment
            if self.phase_elapsed_seconds >= self.phase_duration_seconds:
                self.phase_elapsed_seconds = 0.0
                if self.phase_number >= len(self.PHASES):
                    self.status = ScenarioStatus.COMPLETED
                    with EventTrace.context(scenario_id=self.scenario_id, simulation=True):
                        EventTrace.log_event("DEMO_SCENARIO_COMPLETED", "building", "demo_scenario_engine", {})
                    if self._on_complete is not None:
                        completion = self._on_complete()
                        if inspect.isawaitable(completion):
                            await completion
                    return
                self.phase_number += 1
                await self._emit_phase()
                if self.status != ScenarioStatus.RUNNING:
                    return

    async def pause(self):
        if self.status != ScenarioStatus.RUNNING:
            raise ValueError("Only a running demo scenario can be paused.")
        self.status = ScenarioStatus.PAUSED
        await self._cancel_run_task()
        return self.status_payload()

    async def resume(self):
        if self.status != ScenarioStatus.PAUSED:
            raise ValueError("Only a paused demo scenario can be resumed.")
        self.status = ScenarioStatus.RUNNING
        self._task = asyncio.create_task(self._run())
        return self.status_payload()

    async def stop(self):
        if self.status not in {ScenarioStatus.RUNNING, ScenarioStatus.PAUSED}:
            raise ValueError("Only a running or paused demo scenario can be stopped.")
        self.status = ScenarioStatus.STOPPED
        await self._cancel_run_task()
        return self.status_payload()

    async def reset(self):
        await self._cancel_run_task()
        if self.scenario_id:
            EventTrace.clear_context_events("scenario_id", self.scenario_id)
        self.status = ScenarioStatus.IDLE
        self.elapsed_seconds = self.phase_elapsed_seconds = 0.0
        self.phase_number = 0
        self.started_at = None
        self.scenario_id = None
        self.last_error = None
        self._on_complete = None
        return self.status_payload()

    async def _cancel_run_task(self):
        task = self._task
        self._task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def set_speed(self, speed: float):
        if isinstance(speed, bool) or speed not in self.SPEEDS:
            raise ValueError("Speed must be one of 0.5, 1, 2, or 5.")
        self.speed_multiplier = float(speed)
        return self.status_payload()

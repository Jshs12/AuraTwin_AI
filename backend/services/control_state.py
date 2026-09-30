"""Thread-safe in-memory control mode and provider fail-safe latch per zone."""

from contextlib import contextmanager
from threading import Lock, RLock
from typing import Iterator

from backend.core.events import EventTrace
from backend.core.time import utc_now
from backend.schemas.control import ZoneControlState


class ZoneControlStateService:
    """Serializes operator mode changes with each zone's final provider write."""

    def __init__(self, *, initially_enabled: bool = True):
        self._registry_lock = Lock()
        self._zone_locks: dict[str, RLock] = {}
        self._states: dict[str, ZoneControlState] = {}
        self._announced_blocks: set[tuple[str, str, str]] = set()
        self.initially_enabled = initially_enabled

    def _lock(self, zone_id: str) -> RLock:
        with self._registry_lock:
            return self._zone_locks.setdefault(zone_id, RLock())

    @contextmanager
    def write_guard(self, zone_id: str) -> Iterator[None]:
        """Hold the zone mode lock through final checks and provider write."""
        with self._lock(zone_id):
            yield

    def snapshot(self, zone_id: str) -> ZoneControlState:
        with self._lock(zone_id):
            return self._state(zone_id).model_copy(deep=True)

    def _state(self, zone_id: str) -> ZoneControlState:
        return self._states.setdefault(zone_id, ZoneControlState(
            zone_id=zone_id, control_enabled=self.initially_enabled,
        ))

    def set_manual_override(self, zone_id: str, enabled: bool, *, user_id: str) -> ZoneControlState:
        with self._lock(zone_id):
            current = self._state(zone_id)
            if current.manual_override != enabled:
                state = current.model_copy(update={
                    "manual_override": enabled,
                    "resume_pending": current.resume_pending or enabled,
                    "updated_at": utc_now(), "updated_by": user_id,
                })
                self._states[zone_id] = state
                self._clear_block_announcements(zone_id)
                EventTrace.log_event(
                    "MANUAL_OVERRIDE_ENABLED" if enabled else "MANUAL_OVERRIDE_DISABLED",
                    zone_id, "control_state_service",
                    {"user_id": user_id, "control_enabled": state.control_enabled},
                    status="SUCCESS",
                )
            return self._state(zone_id).model_copy(deep=True)

    def set_control_enabled(self, zone_id: str, enabled: bool, *, user_id: str) -> ZoneControlState:
        with self._lock(zone_id):
            current = self._state(zone_id)
            if current.control_enabled != enabled:
                state = current.model_copy(update={
                    "control_enabled": enabled,
                    "resume_pending": current.resume_pending or not enabled,
                    "updated_at": utc_now(), "updated_by": user_id,
                })
                self._states[zone_id] = state
                self._clear_block_announcements(zone_id)
                EventTrace.log_event(
                    "CONTROL_ENABLED" if enabled else "CONTROL_DISABLED",
                    zone_id, "control_state_service",
                    {"user_id": user_id, "manual_override": state.manual_override,
                     "fail_safe_active": state.fail_safe_active},
                    status="SUCCESS",
                )
            return self._state(zone_id).model_copy(deep=True)

    def note_provider_ready(self, zone_id: str) -> None:
        with self._lock(zone_id):
            current = self._state(zone_id)
            if current.provider_failure_latched and current.provider_unavailable_seen and not current.provider_recovered:
                self._states[zone_id] = current.model_copy(update={
                    "provider_recovered": True, "provider_unavailable_seen": False,
                    "updated_at": utc_now(),
                })
                EventTrace.log_event("PROVIDER_RECOVERY", zone_id, "control_state_service",
                                     {"control_enabled": current.control_enabled,
                                      "fail_safe_active": current.fail_safe_active,
                                      "automatic_resume": False})

    def note_provider_unavailable(self, zone_id: str) -> None:
        with self._lock(zone_id):
            current = self._state(zone_id)
            if current.provider_failure_latched and not current.provider_unavailable_seen:
                self._states[zone_id] = current.model_copy(update={
                    "provider_unavailable_seen": True, "provider_recovered": False,
                    "updated_at": utc_now(),
                })

    def note_provider_failure(self, zone_id: str, *, command_id: str,
                              reason_code: str, provider_ready: bool) -> None:
        with self._lock(zone_id):
            current = self._state(zone_id)
            if not current.fail_safe_active:
                state = current.model_copy(update={
                    "control_enabled": False, "fail_safe_active": True,
                    "provider_failure_latched": True,
                    "provider_unavailable_seen": not provider_ready, "provider_recovered": False,
                    "resume_pending": True, "updated_at": utc_now(),
                })
                self._states[zone_id] = state
                self._clear_block_announcements(zone_id)
                if current.control_enabled:
                    EventTrace.log_event("CONTROL_DISABLED", zone_id, "control_state_service",
                                         {"reason_code": "PROVIDER_FAILURE", "fail_safe": True},
                                         status="FAILED")
                EventTrace.log_event("PROVIDER_FAILURE", zone_id, "control_state_service",
                                     {"command_id": command_id, "reason_code": reason_code}, status="FAILED")
                EventTrace.log_event("FAIL_SAFE_ACTIVATED", zone_id, "control_state_service",
                                     {"command_id": command_id, "control_enabled": False,
                                      "automatic_resume": False}, status="FAILED")
            elif not provider_ready and not current.provider_unavailable_seen:
                self._states[zone_id] = current.model_copy(update={
                    "provider_unavailable_seen": True, "provider_recovered": False,
                    "updated_at": utc_now(),
                })

    def note_command_success(self, zone_id: str, *, command_id: str, simulated: bool) -> None:
        with self._lock(zone_id):
            current = self._state(zone_id)
            if current.fail_safe_active or current.resume_pending:
                self._states[zone_id] = current.model_copy(update={
                    "fail_safe_active": False, "provider_failure_latched": False,
                    "provider_unavailable_seen": False, "provider_recovered": False,
                    "resume_pending": False,
                    "updated_at": utc_now(),
                })
                EventTrace.log_event("AUTO_CONTROL_RESUMED", zone_id, "control_state_service",
                                     {"command_id": command_id, "simulated": simulated})
                self._clear_block_announcements(zone_id)

    def blocked_reason(self, zone_id: str) -> tuple[str, str] | None:
        with self._lock(zone_id):
            state = self._state(zone_id)
            if state.manual_override:
                return "MANUAL_OVERRIDE_ACTIVE", "COMMAND_BLOCKED_BY_OVERRIDE"
            if not state.control_enabled:
                return "CONTROL_DISABLED", "COMMAND_BLOCKED_BY_CONTROL_DISABLE"
            return None

    def note_block(self, zone_id: str, *, command_id: str, reason_code: str, event_type: str) -> None:
        with self._lock(zone_id):
            state = self._state(zone_id)
            signature = (zone_id, event_type, state.updated_at.isoformat())
            if signature not in self._announced_blocks:
                self._announced_blocks.add(signature)
                EventTrace.log_event(event_type, zone_id, "control_state_service",
                                     {"command_id": command_id, "reason_code": reason_code}, status="FAILED")

    def _clear_block_announcements(self, zone_id: str) -> None:
        self._announced_blocks = {entry for entry in self._announced_blocks if entry[0] != zone_id}

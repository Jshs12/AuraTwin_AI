"""Explicit integration lifecycle transitions and bounded retry policy contract."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from backend.database.models import IntegrationLifecycleEventRecord


class ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class BoundedRetryPolicy:
    """No default retry schedule: callers must select explicit bounded values."""
    max_attempts: int
    initial_delay_seconds: float
    maximum_delay_seconds: float

    def delay_for(self, failed_attempt: int) -> float | None:
        if self.max_attempts <= 0 or self.initial_delay_seconds < 0 or self.maximum_delay_seconds < 0:
            raise ValueError("Retry policy must have a positive attempt bound and non-negative delays")
        if failed_attempt >= self.max_attempts:
            return None
        return min(self.initial_delay_seconds * (2 ** max(0, failed_attempt - 1)),
                   self.maximum_delay_seconds)


class IntegrationLifecycleManager:
    """Persist observed adapter transitions; callers supply sanitized error codes."""
    SAFE_ERROR_CODES = {"CONNECTION_FAILED", "CONNECT_TIMEOUT", "AUTHENTICATION_FAILED",
        "ADAPTER_UNAVAILABLE", "PROTOCOL_ERROR", "OBSERVATION_ERROR", "RATE_LIMITED"}
    ALLOWED = {
        ConnectionState.DISCONNECTED: {ConnectionState.CONNECTING},
        ConnectionState.CONNECTING: {ConnectionState.CONNECTED, ConnectionState.DEGRADED,
                                     ConnectionState.ERROR, ConnectionState.DISCONNECTED},
        ConnectionState.CONNECTED: {ConnectionState.DEGRADED, ConnectionState.ERROR,
                                   ConnectionState.DISCONNECTED},
        ConnectionState.DEGRADED: {ConnectionState.CONNECTING, ConnectionState.CONNECTED,
                                   ConnectionState.ERROR, ConnectionState.DISCONNECTED},
        ConnectionState.ERROR: {ConnectionState.DISCONNECTED, ConnectionState.CONNECTING},
    }

    @classmethod
    def transition(cls, session, integration, state: ConnectionState, *, source: str,
                   simulated: bool, error_code: str | None = None):
        previous = ConnectionState(integration.connection_state)
        if integration.status != "CONFIGURED" and state != ConnectionState.DISCONNECTED:
            raise ValueError("Disabled integrations cannot enter an active connection state")
        if state not in cls.ALLOWED[previous]:
            raise ValueError(f"Invalid integration connection transition: {previous} -> {state}")
        if error_code is not None and error_code not in cls.SAFE_ERROR_CODES:
            raise ValueError("Lifecycle errors must use an allow-listed sanitized reason code")
        if (not source.isascii() or len(source) > 100
                or not source.replace("_", "").replace("-", "").isalnum()):
            raise ValueError("Lifecycle source must be a safe provider identifier")
        integration.connection_state = state.value
        integration.last_error = error_code if state in {ConnectionState.ERROR, ConnectionState.DEGRADED} else None
        integration.updated_at = datetime.now(timezone.utc)
        if state in {ConnectionState.ERROR, ConnectionState.DEGRADED}:
            previous_commissioning = integration.commissioning_state
            integration.commissioning_state = "BLOCKED"
            commissioning_next = "BLOCKED"
        elif state == ConnectionState.CONNECTED and integration.commissioning_state != "BLOCKED":
            previous_commissioning = integration.commissioning_state
            integration.commissioning_state = "CONNECTION_TESTED"
            commissioning_next = "CONNECTION_TESTED"
        else:
            previous_commissioning = integration.commissioning_state
            commissioning_next = integration.commissioning_state
        session.add(IntegrationLifecycleEventRecord(integration_id=integration.integration_id,
            event_type=f"CONNECTION_{state.value}", previous_state=previous.value,
            new_state=state.value, source=source, simulated=simulated,
            occurred_at=datetime.now(timezone.utc)))
        if commissioning_next != previous_commissioning:
            session.add(IntegrationLifecycleEventRecord(integration_id=integration.integration_id,
                event_type=f"COMMISSIONING_{commissioning_next}", previous_state=previous_commissioning,
                new_state=commissioning_next, source=source, simulated=simulated,
                occurred_at=datetime.now(timezone.utc)))
        return integration

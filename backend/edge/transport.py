"""Outbound-only transport contracts; simulated mode never opens a network socket."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from backend.schemas.edge import (EdgeDeliveryAcknowledgement, EdgeDeliveryStatus,
                                  EdgeObservationEnvelope)


class OutboundTransportError(RuntimeError):
    """Sanitized transport error contract; never includes headers or raw response content."""

    def __init__(self, reason_code: str, *, retryable: bool):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True)
class TransportBatchResult:
    acknowledgements: tuple[object, ...]
    transport_state: str
    simulated: bool

    @property
    def accepted_message_ids(self) -> tuple:
        """Compatibility projection; callers must still validate full ACK contracts."""
        return tuple(ack.message_id for ack in self.acknowledgements
                     if isinstance(ack, EdgeDeliveryAcknowledgement)
                     and ack.status in {EdgeDeliveryStatus.ACCEPTED, EdgeDeliveryStatus.DUPLICATE})

    @property
    def receiver_results(self) -> tuple[object, ...]:
        return self.acknowledgements


class OutboundTransport(Protocol):
    simulated: bool

    def connect(self) -> str: ...
    def send_observation_batch(self, batch: tuple[EdgeObservationEnvelope, ...]) -> TransportBatchResult: ...
    def acknowledge(self, response: TransportBatchResult) -> tuple[object, ...]: ...
    def heartbeat(self, payload: dict) -> str: ...
    def health(self) -> str: ...
    def disconnect(self) -> None: ...


class SimulatedOutboundTransport:
    """Explicit in-process receiver; ACKs describe local simulation, never cloud delivery."""

    simulated = True
    _SCENARIOS = {"temporary_failure", "permanent_failure", "duplicate_ack",
                   "malformed_ack", "timeout", "unavailable"}

    def __init__(self, receiver: Callable[[EdgeObservationEnvelope], object] | None = None,
                 scenarios: list[str] | None = None):
        self.receiver = receiver
        self.messages: list[EdgeObservationEnvelope] = []
        self.heartbeats: list[dict] = []
        self.active = False
        self.scenarios = list(scenarios or [])
        if any(item not in self._SCENARIOS for item in self.scenarios):
            raise ValueError("Unsupported deterministic simulated transport scenario")

    def connect(self) -> str:
        self.active = True
        return "SIMULATED_IN_PROCESS"

    def _scenario(self) -> str | None:
        return self.scenarios.pop(0) if self.scenarios else None

    def send_observation_batch(self, batch: tuple[EdgeObservationEnvelope, ...]) -> TransportBatchResult:
        if not self.active:
            raise RuntimeError("TRANSPORT_UNAVAILABLE")
        acks = []
        for envelope in batch:
            scenario = self._scenario()
            if scenario == "timeout":
                raise OutboundTransportError("TRANSPORT_TIMEOUT", retryable=True)
            if scenario == "unavailable":
                self.active = False
                raise OutboundTransportError("TRANSPORT_UNAVAILABLE", retryable=True)
            if scenario == "temporary_failure":
                raise OutboundTransportError("TRANSPORT_TEMPORARY_FAILURE", retryable=True)
            if scenario == "permanent_failure":
                raise OutboundTransportError("TRANSPORT_PERMANENT_FAILURE", retryable=False)
            self.messages.append(envelope)
            if scenario == "malformed_ack":
                acks.append({"message_id": str(envelope.message_id), "status": "ACCEPTED"})
                continue
            received = self.receiver(envelope) if self.receiver else None
            if received is None:
                # A capture-only transport has no receiver contract and therefore cannot ACK.
                continue
            if scenario == "duplicate_ack" or bool(getattr(received, "duplicate", False)):
                ack_status = EdgeDeliveryStatus.DUPLICATE
            elif received is not None and not bool(getattr(received, "accepted", False)):
                ack_status = EdgeDeliveryStatus.REJECTED
            else:
                ack_status = EdgeDeliveryStatus.ACCEPTED
            acks.append(EdgeDeliveryAcknowledgement(message_id=envelope.message_id,
                status=ack_status, server_received_at=datetime.now(timezone.utc),
                ingestion_id="simulated-local-ingestion" if received is not None else None))
        return TransportBatchResult(tuple(acks), "SIMULATED_IN_PROCESS", True)

    def heartbeat(self, payload: dict) -> str:
        if not self.active:
            raise RuntimeError("TRANSPORT_UNAVAILABLE")
        self.heartbeats.append(dict(payload))
        return "SIMULATED_CAPTURED"

    def acknowledge(self, response: TransportBatchResult) -> tuple[object, ...]:
        return response.acknowledgements

    def health(self) -> str:
        return "SIMULATED_IN_PROCESS" if self.active else "DISCONNECTED"

    def disconnect(self) -> None:
        self.active = False


class UnavailableRealOutboundTransport:
    """Explicit real-transport placeholder: no endpoint, socket, TLS, or fake connection."""

    simulated = False

    def connect(self) -> str:
        return "UNAVAILABLE"

    def send_observation_batch(self, batch: tuple[EdgeObservationEnvelope, ...]) -> TransportBatchResult:
        raise RuntimeError("TRANSPORT_NOT_CONFIGURED")

    def heartbeat(self, payload: dict) -> str:
        raise RuntimeError("TRANSPORT_NOT_CONFIGURED")

    def acknowledge(self, response: TransportBatchResult) -> tuple[object, ...]:
        raise RuntimeError("TRANSPORT_NOT_CONFIGURED")

    def health(self) -> str:
        return "UNAVAILABLE"

    def disconnect(self) -> None:
        return None

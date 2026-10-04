"""Outbound-only transport contracts; simulated mode never opens a network socket."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from backend.schemas.edge import EdgeObservationEnvelope


@dataclass(frozen=True)
class TransportBatchResult:
    accepted_message_ids: tuple
    receiver_results: tuple[object, ...]
    transport_state: str
    simulated: bool


class OutboundTransport(Protocol):
    simulated: bool

    def connect(self) -> str: ...
    def send_observation_batch(self, batch: tuple[EdgeObservationEnvelope, ...]) -> TransportBatchResult: ...
    def heartbeat(self, payload: dict) -> str: ...
    def health(self) -> str: ...
    def disconnect(self) -> None: ...


class SimulatedOutboundTransport:
    """In-process transport capture/receiver. It never claims cloud/network connectivity."""

    simulated = True

    def __init__(self, receiver: Callable[[EdgeObservationEnvelope], object] | None = None):
        self.receiver = receiver
        self.messages: list[EdgeObservationEnvelope] = []
        self.heartbeats: list[dict] = []
        self.active = False

    def connect(self) -> str:
        self.active = True
        return "SIMULATED_IN_PROCESS"

    def send_observation_batch(self, batch: tuple[EdgeObservationEnvelope, ...]) -> TransportBatchResult:
        if not self.active:
            raise RuntimeError("TRANSPORT_UNAVAILABLE")
        results = []
        for envelope in batch:
            self.messages.append(envelope)
            results.append(self.receiver(envelope) if self.receiver else None)
        return TransportBatchResult(tuple(item.message_id for item in batch), tuple(results),
                                    "SIMULATED_IN_PROCESS", True)

    def heartbeat(self, payload: dict) -> str:
        if not self.active:
            raise RuntimeError("TRANSPORT_UNAVAILABLE")
        self.heartbeats.append(dict(payload))
        return "SIMULATED_CAPTURED"

    def health(self) -> str:
        return "SIMULATED_IN_PROCESS" if self.active else "DISCONNECTED"

    def disconnect(self) -> None:
        self.active = False


class UnavailableRealOutboundTransport:
    """Explicit placeholder; no endpoint, sockets, TLS, or fake real connectivity."""

    simulated = False

    def connect(self) -> str:
        return "UNAVAILABLE"

    def send_observation_batch(self, batch: tuple[EdgeObservationEnvelope, ...]) -> TransportBatchResult:
        raise RuntimeError("TRANSPORT_NOT_CONFIGURED")

    def heartbeat(self, payload: dict) -> str:
        raise RuntimeError("TRANSPORT_NOT_CONFIGURED")

    def health(self) -> str:
        return "UNAVAILABLE"

    def disconnect(self) -> None:
        return None

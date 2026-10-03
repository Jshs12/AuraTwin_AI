"""Typed integration adapter contracts. Contracts describe capabilities; they do not perform I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class AdapterCapabilities:
    protocol: str
    can_test_connection: bool
    can_discover_devices: bool
    can_discover_points: bool
    can_observe: bool
    can_report_health: bool
    can_read: bool = True
    can_write: bool = False
    physical_io: bool = False


@dataclass(frozen=True)
class AdapterHealth:
    state: str
    last_seen_at: datetime | None = None
    last_error: str | None = None
    simulated: bool = True


class IntegrationAdapter(Protocol):
    capabilities: AdapterCapabilities
    def test_connection(self) -> object: ...
    def discover_devices(self) -> object: ...
    def discover_points(self, device_identifier: str) -> object: ...
    def observe(self, point_identifier: str) -> object: ...
    def health(self) -> AdapterHealth: ...
    def close(self) -> None: ...


class BacnetIpAdapter(IntegrationAdapter, Protocol):
    """BACnet capability metadata includes read/write support; no physical write is implemented."""


class RtspCameraAdapter(IntegrationAdapter, Protocol):
    """RTSP contract; frames/streams are not fetched or persisted in this phase."""


class EnergyMeterAdapter(IntegrationAdapter, Protocol):
    """Meter contract; no physical meter polling is implemented in this phase."""

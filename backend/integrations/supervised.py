"""Supervised read-only adapter boundary.

The registry intentionally defaults to explicit unavailable adapters. Registering a
physical driver is a separate deployment decision; fixtures never impersonate it.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable

from backend.integrations.contracts import AdapterCapabilities, AdapterHealth


@dataclass(frozen=True)
class AdapterActionResult:
    succeeded: bool
    simulated: bool
    physical_io: bool
    reason_code: str | None = None


class AdapterTimeout(Exception):
    pass


class ReadOnlyAdapterUnavailable(Exception):
    pass


class UnavailableReadOnlyAdapter:
    """Placeholder for a physical protocol with no driver configured in this build."""

    protocol = "UNAVAILABLE"
    capabilities = AdapterCapabilities(
        protocol="UNAVAILABLE", can_test_connection=True,
        can_discover_devices=False, can_discover_points=False, can_observe=False,
        can_report_health=True, can_read=False, can_write=False, physical_io=False,
    )

    def test_connection(self) -> AdapterActionResult:
        return AdapterActionResult(False, simulated=True, physical_io=False,
                                    reason_code="ADAPTER_UNAVAILABLE")

    def discover_devices(self):
        raise ReadOnlyAdapterUnavailable("ADAPTER_UNAVAILABLE")

    def discover_points(self, device_identifier: str):
        raise ReadOnlyAdapterUnavailable("ADAPTER_UNAVAILABLE")

    def observe(self, point_identifier: str):
        raise ReadOnlyAdapterUnavailable("ADAPTER_UNAVAILABLE")

    def health(self) -> AdapterHealth:
        return AdapterHealth(state="UNAVAILABLE", last_error="ADAPTER_UNAVAILABLE", simulated=True)

    def close(self) -> None:
        return None


class UnavailableBacnetIpAdapter(UnavailableReadOnlyAdapter):
    protocol = "BACNET/IP"
    capabilities = AdapterCapabilities("BACNET/IP", True, False, False, False, True,
                                        can_read=False, can_write=False, physical_io=False)


class UnavailableRtspCameraAdapter(UnavailableReadOnlyAdapter):
    protocol = "RTSP"
    capabilities = AdapterCapabilities("RTSP", True, False, False, False, True,
                                        can_read=False, can_write=False, physical_io=False)


class UnavailableEnergyMeterAdapter(UnavailableReadOnlyAdapter):
    protocol = "ENERGY_METER"
    capabilities = AdapterCapabilities("ENERGY_METER", True, False, False, False, True,
                                        can_read=False, can_write=False, physical_io=False)


DEFAULT_ADAPTERS: dict[str, type[UnavailableReadOnlyAdapter]] = {
    "BACNET": UnavailableBacnetIpAdapter,
    "CAMERA": UnavailableRtspCameraAdapter,
    "ENERGY_METER": UnavailableEnergyMeterAdapter,
}


class AdapterRegistry:
    """Small injectable registry; no adapter is created from untrusted config data."""

    def __init__(self, factories: dict[str, Callable[[dict], object]] | None = None):
        self._factories: dict[str, Callable[[dict], object]] = {
            key: (lambda _configuration, cls=adapter_type: cls())
            for key, adapter_type in DEFAULT_ADAPTERS.items()
        }
        if factories:
            self._factories.update({key.upper(): value for key, value in factories.items()})
        self._active: dict[str, object] = {}

    def create(self, integration_id: str, integration_type: str, configuration: dict | None = None):
        factory = self._factories.get(integration_type.upper())
        return factory(dict(configuration or {})) if factory else None

    def register(self, integration_type: str, factory: Callable[[dict], object]) -> None:
        self._factories[integration_type.upper()] = factory

    def active(self, integration_id: str):
        return self._active.get(integration_id)

    def set_active(self, integration_id: str, adapter) -> None:
        self._active[integration_id] = adapter

    def remove_active(self, integration_id: str):
        return self._active.pop(integration_id, None)

    def active_items(self):
        return tuple(self._active.items())


def run_bounded(operation: Callable, timeout_seconds: float):
    """Run a synchronous adapter operation with a bounded caller wait."""
    if timeout_seconds <= 0:
        raise ValueError("Adapter timeout must be positive")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="auratwin-adapter")
    future = executor.submit(operation)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeout as exc:
        future.cancel()
        raise AdapterTimeout from exc
    finally:
        # A timed-out synchronous driver cannot be forcibly stopped by Python.
        # Never wait on it on the request path; physical adapters must implement
        # their own socket/read timeout as well as this outer bound.
        executor.shutdown(wait=False, cancel_futures=True)

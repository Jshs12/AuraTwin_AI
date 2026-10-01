"""Discovery abstraction; the Phase 11.5 implementation performs no device discovery."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DiscoveryResult:
    simulated: bool
    performed: bool
    candidates: tuple[dict, ...]
    message: str


class DiscoveryProvider(Protocol):
    def discover(self, integration_type: str) -> DiscoveryResult: ...


class NoDiscoveryProvider:
    def discover(self, integration_type: str) -> DiscoveryResult:
        return DiscoveryResult(simulated=True, performed=False, candidates=(),
            message="Device discovery is not implemented; no network discovery was attempted.")

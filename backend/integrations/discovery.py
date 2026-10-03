"""Discovery contracts and deterministic fixtures (fixtures are never physical discovery)."""
from dataclasses import dataclass
from datetime import datetime, timezone
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


class SimulatedFixtureDiscoveryProvider:
    """Repeatable integration fixtures represented in the existing device/point schema."""
    simulated = True

    def discover(self, integration_type: str) -> DiscoveryResult:
        fixtures = {
            "BACNET": ({"external_device_id": "fixture:bacnet:controller-01",
                "name": "Simulated BACnet controller", "device_type": "HVAC_CONTROLLER",
                "protocol": "BACNET/IP", "source": "SIMULATED_FIXTURE", "capabilities": ["read_metadata", "write_metadata"],
                "points": [
                    {"external_point_id": "fixture:bacnet:controller-01:space-temp", "name": "Space temperature", "logical_signal": "temperature", "data_type": "number", "unit": "°C", "readable": True, "writable": False},
                    {"external_point_id": "fixture:bacnet:controller-01:cooling-sp", "name": "Cooling setpoint", "logical_signal": "cooling_setpoint", "data_type": "number", "unit": "°C", "readable": True, "writable": True},
                ]},),
            "CAMERA": ({"external_device_id": "fixture:camera:occupancy-01",
                "name": "Simulated occupancy camera", "device_type": "CAMERA",
                "protocol": "RTSP", "source": "SIMULATED_FIXTURE", "capabilities": ["occupancy_metadata"],
                "points": [{"external_point_id": "fixture:camera:occupancy-01:people", "name": "Person count", "logical_signal": "occupancy", "data_type": "integer", "unit": "people", "readable": True, "writable": False}]},),
            "ENERGY_METER": ({"external_device_id": "fixture:meter:energy-01",
                "name": "Simulated energy meter", "device_type": "ENERGY_METER",
                "protocol": "METER", "source": "SIMULATED_FIXTURE", "capabilities": ["power_metadata", "energy_metadata"],
                "points": [{"external_point_id": "fixture:meter:energy-01:power", "name": "Power", "logical_signal": "power", "data_type": "number", "unit": "kW", "readable": True, "writable": False},
                    {"external_point_id": "fixture:meter:energy-01:energy", "name": "Cumulative energy", "logical_signal": "energy", "data_type": "number", "unit": "kWh", "readable": True, "writable": False}]},),
        }
        candidates = fixtures.get(integration_type)
        if candidates is None:
            return DiscoveryResult(simulated=True, performed=False, candidates=(),
                message="No fixture exists for this integration type; no discovery was attempted.")
        timestamp = datetime.now(timezone.utc).isoformat()
        enriched = tuple({**candidate, "discovery_timestamp": timestamp,
                          "quality_status": "SIMULATED_FIXTURE", "error": None,
                          "protocol": candidate["protocol"], "source": "SIMULATED_FIXTURE",
                          "points": [{**point, "discovery_timestamp": timestamp,
                              "quality_status": "SIMULATED_FIXTURE", "error": None,
                              "source": "SIMULATED_FIXTURE", "protocol": candidate["protocol"]}
                              for point in candidate.get("points", [])]}
                         for candidate in candidates)
        return DiscoveryResult(simulated=True, performed=True, candidates=enriched,
            message="SIMULATED FIXTURES only. No network or physical discovery was performed.")

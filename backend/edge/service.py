"""Building-scoped edge runtime over existing adapters and observation ingestion."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.core.events import EventTrace
from backend.database.models import BuildingRecord, DeviceRecord, IntegrationRecord, PointMappingRecord
from backend.edge.buffer import BoundedObservationBuffer
from backend.edge.transport import (OutboundTransport, SimulatedOutboundTransport,
                                    UnavailableRealOutboundTransport)
from backend.integrations.supervised import AdapterTimeout, run_bounded
from backend.schemas.data_quality import QualityState
from backend.schemas.edge import (EdgeCapability, EdgeConfiguration, EdgeHealth,
                                  EdgeHeartbeat, EdgeLifecycleState, EdgeMode,
                                  EdgeObservationEnvelope)
from backend.schemas.provider_observation import ProviderObservation


_PROTOCOL_CAPABILITIES = {
    "BACNET": EdgeCapability.READ_BACNET,
    "BACNET/IP": EdgeCapability.READ_BACNET,
    "CAMERA": EdgeCapability.READ_CAMERA,
    "RTSP": EdgeCapability.READ_CAMERA,
    "ENERGY_METER": EdgeCapability.READ_ENERGY_METER,
}


def edge_configuration_from_environment() -> tuple[EdgeConfiguration | None, str | None]:
    """Return explicit process configuration without reading/storing secrets."""
    mode_value = os.environ.get("AURATWIN_EDGE_MODE", "simulated").strip().lower()
    try:
        mode = EdgeMode(mode_value)
    except ValueError:
        return None, "EDGE_MODE_INVALID"
    edge_id = os.environ.get("AURATWIN_EDGE_ID", "").strip()
    building_id = os.environ.get("AURATWIN_EDGE_BUILDING_ID", "").strip()
    if not edge_id or not building_id:
        return None, "EDGE_IDENTITY_NOT_CONFIGURED"
    try:
        capacity = int(os.environ.get("AURATWIN_EDGE_MAX_BUFFER_MESSAGES", "100"))
        config = EdgeConfiguration(
            edge_id=edge_id,
            building_id=building_id,
            expected_organization_id=os.environ.get("AURATWIN_EDGE_ORGANIZATION_ID") or None,
            name=os.environ.get("AURATWIN_EDGE_NAME", "AuraTwin Edge Connector"),
            version=os.environ.get("AURATWIN_EDGE_VERSION", "13.4A"),
            mode=mode,
            max_buffer_messages=capacity,
        )
        return config, None
    except (ValueError, TypeError):
        return None, "EDGE_CONFIGURATION_INVALID"


class EdgeConnectorService:
    """Process-local edge orchestrator; integration and cloud ingestion remain authoritative."""

    def __init__(self, *, configuration, sessions, adapter_registry, ingestion_service,
                 config: EdgeConfiguration | None = None, configuration_error: str | None = None,
                 transport: OutboundTransport | None = None, adapter_timeout_seconds: float = 5.0):
        self.configuration = configuration
        self.sessions = sessions
        self.adapter_registry = adapter_registry
        self.ingestion_service = ingestion_service
        self.config = config
        self.configuration_error = configuration_error
        self.adapter_timeout_seconds = adapter_timeout_seconds
        self.state = EdgeLifecycleState.STOPPED
        self.transport_state = "NOT_CONFIGURED"
        self.reason_code: str | None = configuration_error
        self.organization_id: str | None = None
        self.building_id: str | None = None
        self.last_heartbeat: datetime | None = None
        self.last_observation_forwarded: datetime | None = None
        self.accepting = False
        self.buffer = BoundedObservationBuffer(config.max_buffer_messages if config else 100)
        self.transport = transport
        self._started = False

    @classmethod
    def from_environment(cls, *, configuration, sessions, adapter_registry,
                          ingestion_service, adapter_timeout_seconds: float = 5.0):
        config, error = edge_configuration_from_environment()
        return cls(configuration=configuration, sessions=sessions,
                   adapter_registry=adapter_registry, ingestion_service=ingestion_service,
                   config=config, configuration_error=error,
                   adapter_timeout_seconds=adapter_timeout_seconds)

    @property
    def simulated(self) -> bool:
        return self.config is not None and self.config.mode == EdgeMode.SIMULATED

    def _log(self, event_type: str, payload: dict, *, failed: bool = False) -> None:
        edge_key = self.config.edge_id if self.config else "unconfigured"
        EventTrace.log_event(event_type, f"edge:{edge_key}", "edge_connector",
                             {"edge_id": self.config.edge_id if self.config else None,
                              "building_id": self.building_id, **payload},
                             status="FAILED" if failed else "SUCCESS")

    def start(self) -> EdgeHealth:
        if self._started and self.state in {EdgeLifecycleState.RUNNING, EdgeLifecycleState.DEGRADED}:
            return self.status()
        self.state = EdgeLifecycleState.STARTING
        self._log("EDGE_STARTING", {})
        if self.config is None:
            self.reason_code = self.configuration_error or "EDGE_IDENTITY_NOT_CONFIGURED"
            self.state = EdgeLifecycleState.DEGRADED
            self.transport_state = "NOT_CONFIGURED"
            self._started = True
            self._log("EDGE_DEGRADED", {"reason_code": self.reason_code}, failed=True)
            return self.status()
        building = self.configuration.get_building(self.config.building_id)
        if building is None:
            self.reason_code = "EDGE_BUILDING_NOT_FOUND"
            self.state = EdgeLifecycleState.ERROR
            self.transport_state = "NOT_CONFIGURED"
            self._started = True
            self._log("EDGE_ERROR", {"reason_code": self.reason_code}, failed=True)
            return self.status()
        actual_building_id = str(building["building_id"])
        actual_organization_id = str(building["organization_id"])
        if (building.get("archived_at") is not None or
                (self.config.expected_organization_id is not None and
                 self.config.expected_organization_id != actual_organization_id)):
            self.reason_code = "EDGE_OWNERSHIP_MISMATCH"
            self.state = EdgeLifecycleState.ERROR
            self.transport_state = "NOT_CONFIGURED"
            self._started = True
            self._log("EDGE_ERROR", {"reason_code": self.reason_code}, failed=True)
            return self.status()
        self.building_id = actual_building_id
        self.organization_id = actual_organization_id
        if self.config.mode == EdgeMode.SIMULATED:
            if self.transport is None:
                self.transport = SimulatedOutboundTransport(self._receive_simulated)
            self.transport_state = self.transport.connect()
            self.state = EdgeLifecycleState.RUNNING
            self.reason_code = None
            self._started = True
            self._log("EDGE_STARTED", {"mode": "simulated", "transport_state": self.transport_state,
                                       "simulated": True})
        else:
            if self.transport is None:
                self.transport = UnavailableRealOutboundTransport()
            self.transport_state = self.transport.connect()
            self.state = EdgeLifecycleState.DEGRADED
            self.reason_code = "TRANSPORT_NOT_CONFIGURED"
            self._started = True
            self._log("EDGE_DEGRADED", {"mode": "real", "transport_state": self.transport_state,
                                        "reason_code": self.reason_code, "simulated": False}, failed=True)
        self.accepting = True
        self.heartbeat()
        return self.status()

    def _enabled_capabilities(self) -> tuple[EdgeCapability, ...]:
        if self.config is None or self.building_id is None:
            return ()
        capabilities = {EdgeCapability.FORWARD_OBSERVATIONS, EdgeCapability.HEARTBEAT}
        for integration_id, adapter in self.adapter_registry.active_items():
            try:
                integration = self._integration_scope(integration_id)
            except Exception:
                continue
            if integration is None or integration[0] != self.building_id or integration[1] != self.organization_id:
                continue
            adapter_caps = getattr(adapter, "capabilities", None)
            if (adapter_caps is not None and getattr(adapter_caps, "can_observe", False)
                    and not getattr(adapter_caps, "can_write", False)):
                capability = _PROTOCOL_CAPABILITIES.get(str(getattr(adapter_caps, "protocol", "")).upper())
                if capability:
                    capabilities.add(capability)
        return tuple(sorted(capabilities, key=lambda item: item.value))

    def status(self, requested_building_id: str | None = None) -> EdgeHealth:
        if requested_building_id and self.building_id and requested_building_id != self.building_id:
            return EdgeHealth(building_id=requested_building_id, version="13.4A",
                mode=self.config.mode if self.config else EdgeMode.SIMULATED,
                state=EdgeLifecycleState.STOPPED, transport_state="NOT_CONFIGURED",
                queue_depth=0, max_buffer_messages=0, simulated=False, healthy=False,
                reason_code="EDGE_NOT_CONFIGURED_FOR_BUILDING")
        if requested_building_id and not self.building_id:
            return EdgeHealth(building_id=requested_building_id, version="13.4A",
                mode=self.config.mode if self.config else EdgeMode.SIMULATED,
                state=EdgeLifecycleState.DEGRADED, transport_state="NOT_CONFIGURED",
                queue_depth=self.buffer.depth, max_buffer_messages=self.buffer.capacity,
                simulated=False, healthy=False,
                reason_code=self.configuration_error or "EDGE_IDENTITY_NOT_CONFIGURED")
        building_id = self.building_id or (self.config.building_id if self.config else "unconfigured")
        mode = self.config.mode if self.config else EdgeMode.SIMULATED
        simulated = self.simulated
        healthy = self.state == EdgeLifecycleState.RUNNING and simulated
        return EdgeHealth(edge_id=self.config.edge_id if self.config else None,
            organization_id=self.organization_id, building_id=building_id,
            name=self.config.name if self.config else None,
            version=self.config.version if self.config else "13.4A", mode=mode,
            state=self.state, transport_state=self.transport_state,
            queue_depth=self.buffer.depth, max_buffer_messages=self.buffer.capacity,
            last_heartbeat=self.last_heartbeat,
            last_observation_forwarded=self.last_observation_forwarded,
            simulated=simulated, capabilities=self._enabled_capabilities(), healthy=healthy,
            reason_code=self.reason_code)

    def heartbeat(self) -> EdgeHeartbeat:
        now = datetime.now(timezone.utc)
        self.last_heartbeat = now
        heartbeat = EdgeHeartbeat(edge_id=self.config.edge_id if self.config else "unconfigured",
            organization_id=self.organization_id or "unresolved",
            building_id=self.building_id or (self.config.building_id if self.config else "unconfigured"),
            connector_version=self.config.version if self.config else "13.4A", timestamp=now,
            lifecycle_state=self.state, transport_state=self.transport_state,
            queue_depth=self.buffer.depth, last_observation_timestamp=self.last_observation_forwarded,
            simulated=self.simulated, capabilities=self._enabled_capabilities())
        if self.transport is not None and self.simulated and self.state in {
                EdgeLifecycleState.RUNNING, EdgeLifecycleState.DEGRADED}:
            try:
                self.transport.heartbeat(heartbeat.model_dump(mode="json"))
            except Exception:
                self.transport_state = "SIMULATED_ERROR"
                self.state = EdgeLifecycleState.DEGRADED
                self.reason_code = "HEARTBEAT_FAILED"
        self._log("EDGE_HEARTBEAT", {"transport_state": self.transport_state,
                                     "queue_depth": self.buffer.depth,
                                     "simulated": self.simulated})
        return heartbeat.model_copy(update={"transport_state": self.transport_state,
                                            "lifecycle_state": self.state,
                                            "queue_depth": self.buffer.depth})

    def _integration_scope(self, integration_id: str) -> tuple[str, str] | None:
        try:
            identifier = UUID(integration_id)
        except (ValueError, TypeError):
            return None
        with self.sessions() as session:
            row = session.get(IntegrationRecord, identifier)
            if row is None or row.archived_at is not None:
                return None
            building = session.get(BuildingRecord, row.building_id)
            if building is None or building.archived_at is not None:
                return None
            return str(building.building_id), str(building.organization_id)

    def _receive_simulated(self, envelope: EdgeObservationEnvelope):
        """In-process simulation of the existing cloud ingestion boundary."""
        observation = ProviderObservation(integration_id=envelope.integration_id,
            device_id=envelope.device_id, point_mapping_id=envelope.point_id,
            observed_at=envelope.observation_timestamp, value=envelope.value,
            source=envelope.source, simulated=envelope.simulated,
            quality_state=QualityState(envelope.quality) if envelope.quality in QualityState._value2member_map_ else None,
            runtime_input=envelope.signal in {"occupancy", "temperature", "cooling_setpoint"},
            signal=envelope.signal, unit=envelope.unit)
        # This service re-resolves ownership/mapping and remains authoritative.
        return self.ingestion_service.ingest(observation)

    def forward_observation(self, observation: ProviderObservation) -> dict:
        if not self.accepting or self.config is None or self.building_id is None:
            return self._reject("EDGE_NOT_ACCEPTING")
        if observation.simulated != self.simulated:
            return self._reject("PROVENANCE_MODE_MISMATCH", observation=observation)
        resolved, reason = self.ingestion_service.resolve(observation)
        if resolved is None:
            return self._reject(reason or "OBSERVATION_SCOPE_INVALID", observation=observation)
        if resolved.building_id != self.building_id or resolved.organization_id != self.organization_id:
            return self._reject("EDGE_OWNERSHIP_MISMATCH", observation=observation)
        quality = observation.quality_state.value if observation.quality_state is not None else "UNASSESSED"
        now = datetime.now(timezone.utc)
        try:
            envelope = EdgeObservationEnvelope(message_id=uuid4(), edge_id=self.config.edge_id,
                organization_id=resolved.organization_id, building_id=resolved.building_id,
                integration_id=observation.integration_id, device_id=observation.device_id,
                point_id=observation.point_mapping_id, zone_id=resolved.zone_id,
                signal=resolved.signal.value, value=observation.value, unit=resolved.unit,
                observation_timestamp=observation.observed_at, ingestion_timestamp=now,
                source=observation.source, quality=quality, simulated=observation.simulated)
        except Exception:
            return self._reject("OBSERVATION_ENVELOPE_INVALID", observation=observation)
        if not self.buffer.enqueue(envelope):
            self.reason_code = "BUFFER_FULL"
            self.state = EdgeLifecycleState.DEGRADED
            self._log("EDGE_BUFFER_OVERFLOW", {"message_id": str(envelope.message_id),
                "overflow_policy": self.buffer.OVERFLOW_POLICY, "queue_depth": self.buffer.depth}, failed=True)
            self._log("EDGE_OBSERVATION_REJECTED", {"reason_code": "BUFFER_FULL",
                "message_id": str(envelope.message_id)}, failed=True)
            return {"accepted": False, "reason_code": "BUFFER_FULL", "message_id": str(envelope.message_id)}
        self._log("EDGE_BUFFERED", {"message_id": str(envelope.message_id),
                                    "queue_depth": self.buffer.depth})
        if self.transport is None or self.transport.health() == "UNAVAILABLE":
            self.state = EdgeLifecycleState.DEGRADED
            self.transport_state = "UNAVAILABLE"
            self.reason_code = "TRANSPORT_NOT_CONFIGURED"
            self._log("EDGE_TRANSPORT_UNAVAILABLE", {"queue_depth": self.buffer.depth}, failed=True)
            return {"accepted": True, "forwarded": False, "buffered": True,
                    "reason_code": self.reason_code, "message_id": str(envelope.message_id)}
        return self._drain()

    def _reject(self, reason_code: str, *, observation: ProviderObservation | None = None) -> dict:
        payload = {"reason_code": reason_code}
        if observation is not None:
            payload.update({"integration_id": observation.integration_id,
                            "point_id": observation.point_mapping_id,
                            "simulated": observation.simulated})
        self._log("EDGE_OBSERVATION_REJECTED", payload, failed=True)
        return {"accepted": False, "reason_code": reason_code}

    def _drain(self) -> dict:
        batch = self.buffer.peek()
        if not batch:
            return {"accepted": True, "forwarded": False, "buffered": False, "queue_depth": 0}
        was_degraded = self.state == EdgeLifecycleState.DEGRADED
        try:
            result = self.transport.send_observation_batch(batch)
        except Exception:
            self.state = EdgeLifecycleState.DEGRADED
            self.transport_state = self.transport.health()
            self.reason_code = "TRANSPORT_SEND_FAILED"
            self._log("EDGE_TRANSPORT_UNAVAILABLE", {"reason_code": self.reason_code,
                "queue_depth": self.buffer.depth}, failed=True)
            return {"accepted": True, "forwarded": False, "buffered": True,
                    "reason_code": self.reason_code, "queue_depth": self.buffer.depth}
        accepted_ids = tuple(result.accepted_message_ids)
        self.buffer.acknowledge(accepted_ids)
        self.transport_state = result.transport_state
        now = datetime.now(timezone.utc)
        outcomes = []
        for envelope, receiver_result in zip(batch, result.receiver_results):
            if envelope.message_id not in accepted_ids:
                continue
            accepted = bool(getattr(receiver_result, "accepted", True))
            payload = {"message_id": str(envelope.message_id), "integration_id": envelope.integration_id,
                "signal": envelope.signal, "zone_id": envelope.zone_id,
                "simulated": envelope.simulated,
                "transport_state": result.transport_state}
            self._log("EDGE_OBSERVATION_FORWARDED", payload)
            if not accepted:
                self._log("EDGE_OBSERVATION_REJECTED", {**payload,
                    "reason_code": getattr(receiver_result, "reason_code", None)}, failed=True)
            outcomes.append({"message_id": str(envelope.message_id),
                             "ingestion_accepted": accepted,
                             "reason_code": getattr(receiver_result, "reason_code", None)})
            self.last_observation_forwarded = now
        if self.buffer.depth == 0 and self.simulated:
            self.state = EdgeLifecycleState.RUNNING
            self.reason_code = None
            if was_degraded:
                self._log("EDGE_RECOVERED", {"transport_state": self.transport_state,
                                              "queue_depth": 0, "simulated": True})
        elif self.buffer.depth:
            self.state = EdgeLifecycleState.DEGRADED
            self.reason_code = "BUFFER_NOT_EMPTY"
        return {"accepted": True, "forwarded": bool(accepted_ids), "buffered": self.buffer.depth > 0,
                "transport_state": self.transport_state, "queue_depth": self.buffer.depth,
                "messages": outcomes, "simulated": result.simulated}

    def flush(self) -> dict:
        """Make one explicit bounded FIFO drain attempt; there is no background retry loop."""
        if not self.accepting:
            return {"accepted": False, "forwarded": False, "buffered": self.buffer.depth > 0,
                    "reason_code": "EDGE_NOT_ACCEPTING", "queue_depth": self.buffer.depth}
        if self.transport is None or self.transport.health() == "UNAVAILABLE":
            self.state = EdgeLifecycleState.DEGRADED
            self.transport_state = "UNAVAILABLE"
            self.reason_code = "TRANSPORT_NOT_CONFIGURED"
            return {"accepted": False, "forwarded": False, "buffered": self.buffer.depth > 0,
                    "reason_code": self.reason_code, "queue_depth": self.buffer.depth}
        return self._drain()

    def poll_integration(self, integration_id: str) -> dict:
        """Explicit read-only orchestration over an already-connected supervised adapter."""
        if not self.accepting or self.config is None or self.building_id is None:
            return {"accepted": False, "reason_code": "EDGE_NOT_ACCEPTING", "observations": []}
        try:
            integration_uuid = UUID(integration_id)
        except (ValueError, TypeError):
            return {"accepted": False, "reason_code": "INTEGRATION_NOT_FOUND", "observations": []}
        with self.sessions() as session:
            integration = session.get(IntegrationRecord, integration_uuid)
            if integration is None or integration.archived_at is not None:
                return {"accepted": False, "reason_code": "INTEGRATION_NOT_FOUND", "observations": []}
            building = session.get(BuildingRecord, integration.building_id)
            if (building is None or str(integration.building_id) != self.building_id or
                    str(building.organization_id) != self.organization_id):
                self._log("EDGE_OBSERVATION_REJECTED", {"reason_code": "EDGE_OWNERSHIP_MISMATCH",
                    "integration_id": integration_id}, failed=True)
                return {"accepted": False, "reason_code": "EDGE_OWNERSHIP_MISMATCH", "observations": []}
            if integration.connection_state != "CONNECTED":
                return {"accepted": False, "reason_code": "ADAPTER_NOT_CONNECTED", "observations": []}
            points = session.scalars(select(PointMappingRecord).join(DeviceRecord).where(
                DeviceRecord.integration_id == integration.integration_id,
                DeviceRecord.status == "CONFIGURED", DeviceRecord.archived_at.is_(None),
                PointMappingRecord.mapping_status == "CONFIRMED",
                PointMappingRecord.readable.is_(True))).all()
            point_data = [(str(point.point_mapping_id), str(point.device_id), point.external_point_id)
                          for point in points]
        adapter = self.adapter_registry.active(integration_id)
        capabilities = getattr(adapter, "capabilities", None)
        if (adapter is None or capabilities is None or not getattr(capabilities, "can_observe", False)
                or getattr(capabilities, "can_write", False)):
            return {"accepted": False, "reason_code": "READ_ONLY_ADAPTER_UNAVAILABLE", "observations": []}
        outcomes = []
        for point_id, device_id, external_point_id in point_data:
            try:
                observation = run_bounded(lambda: adapter.observe(external_point_id),
                                          self.adapter_timeout_seconds)
                if not isinstance(observation, ProviderObservation):
                    self._log("EDGE_OBSERVATION_REJECTED", {"integration_id": integration_id,
                        "point_id": point_id, "reason_code": "OBSERVATION_FORMAT_INVALID"}, failed=True)
                    outcomes.append({"accepted": False, "reason_code": "OBSERVATION_FORMAT_INVALID"})
                    continue
                if (observation.integration_id != integration_id or observation.device_id != device_id
                        or observation.point_mapping_id != point_id):
                    self._log("EDGE_OBSERVATION_REJECTED", {"integration_id": integration_id,
                        "point_id": point_id, "reason_code": "OBSERVATION_IDENTITY_MISMATCH"}, failed=True)
                    outcomes.append({"accepted": False, "reason_code": "OBSERVATION_IDENTITY_MISMATCH"})
                    continue
                outcomes.append(self.forward_observation(observation))
            except AdapterTimeout:
                self._log("EDGE_DEGRADED", {"integration_id": integration_id,
                    "point_id": point_id, "reason_code": "OBSERVATION_TIMEOUT"}, failed=True)
                outcomes.append({"accepted": False, "reason_code": "OBSERVATION_TIMEOUT"})
            except Exception:
                self._log("EDGE_DEGRADED", {"integration_id": integration_id,
                    "point_id": point_id, "reason_code": "OBSERVATION_ERROR"}, failed=True)
                outcomes.append({"accepted": False, "reason_code": "OBSERVATION_ERROR"})
        return {"accepted": all(item.get("accepted", False) for item in outcomes),
                "observations": outcomes, "read_only": True, "write_capability": False}

    def stop(self) -> EdgeHealth:
        if not self._started:
            self.state = EdgeLifecycleState.STOPPED
            self.accepting = False
            return self.status()
        self.state = EdgeLifecycleState.STOPPING
        self.accepting = False
        self._log("EDGE_STOPPING", {"queue_depth": self.buffer.depth})
        # A single bounded in-process drain attempt; unavailable transport retains FIFO messages.
        if self.transport is not None and self.transport.health() != "UNAVAILABLE":
            self._drain()
        if self.transport is not None:
            try:
                self.transport.disconnect()
            except Exception:
                pass
        self.transport_state = "DISCONNECTED" if not self.simulated else "SIMULATED_IN_PROCESS"
        self.state = EdgeLifecycleState.STOPPED
        self.reason_code = "BUFFER_RETAINED_ON_SHUTDOWN" if self.buffer.depth else None
        self._started = False
        self._log("EDGE_STOPPED", {"queue_depth": self.buffer.depth,
                                   "reason_code": self.reason_code,
                                   "simulated": self.simulated})
        return self.status()

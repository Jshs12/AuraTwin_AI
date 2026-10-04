from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.edge.buffer import BoundedObservationBuffer
from backend.edge.service import EdgeConnectorService
from backend.edge.transport import SimulatedOutboundTransport, UnavailableRealOutboundTransport
from backend.core.events import EventTrace
from backend.schemas.data_quality import QualityState
from backend.schemas.edge import (EdgeCapability, EdgeConfiguration, EdgeLifecycleState,
                                  EdgeMode, EdgeObservationEnvelope)
from backend.schemas.provider_observation import ProviderObservation
from backend.edge.service import edge_configuration_from_environment


ORG = "org-1"
BUILDING = "building-1"


class FakeConfiguration:
    def get_building(self, building_id):
        if building_id not in {BUILDING, "building-key"}:
            return None
        return {"building_id": BUILDING, "organization_id": ORG, "archived_at": None}


class FakeIngestion:
    def __init__(self, *, resolved_building=BUILDING, resolved_org=ORG, outcome=None):
        self.resolved_building = resolved_building
        self.resolved_org = resolved_org
        self.observations = []
        self.outcome = outcome or SimpleNamespace(accepted=True, reason_code=None)

    def resolve(self, observation):
        return SimpleNamespace(organization_id=self.resolved_org,
            building_id=self.resolved_building, zone_id="zone-1", zone_key="room-1",
            floor_id="floor-1", signal=SimpleNamespace(value=observation.signal or "temperature"),
            unit=observation.unit or "°C", protocol="BACNET"), None

    def ingest(self, observation):
        self.observations.append(observation)
        return self.outcome


class EmptyRegistry:
    def active_items(self):
        return ()

    def active(self, _integration_id):
        return None


def make_service(*, mode=EdgeMode.SIMULATED, capacity=3, ingestion=None,
                 transport=None, expected_org=ORG):
    config = EdgeConfiguration(edge_id="edge-test", building_id=BUILDING,
        expected_organization_id=expected_org, mode=mode, max_buffer_messages=capacity)
    service = EdgeConnectorService(configuration=FakeConfiguration(), sessions=None,
        adapter_registry=EmptyRegistry(), ingestion_service=ingestion or FakeIngestion(),
        config=config, transport=transport)
    return service


def observation(*, simulated=True, signal="temperature", integration_id=None,
                device_id=None, point_id=None, quality=None):
    return ProviderObservation(integration_id=integration_id or str(uuid4()),
        device_id=device_id or str(uuid4()), point_mapping_id=point_id or str(uuid4()),
        observed_at=datetime.now(timezone.utc), value=22.5, source="simulated_edge_test",
        simulated=simulated, quality_state=quality, signal=signal, unit="°C")


def test_simulated_edge_startup_shutdown_and_heartbeat_are_explicit():
    service = make_service()
    started = service.start()
    assert started.state == EdgeLifecycleState.RUNNING
    assert started.simulated is True
    assert started.transport_state == "SIMULATED_IN_PROCESS"
    assert started.healthy is True
    beat = service.heartbeat()
    assert beat.edge_id == "edge-test" and beat.building_id == BUILDING
    assert beat.simulated is True
    assert EdgeCapability.HEARTBEAT in beat.capabilities
    stopped = service.stop()
    assert stopped.state == EdgeLifecycleState.STOPPED


def test_building_organization_binding_is_verified_at_startup():
    service = make_service(expected_org="different-org")
    health = service.start()
    assert health.state == EdgeLifecycleState.ERROR
    assert health.reason_code == "EDGE_OWNERSHIP_MISMATCH"
    assert service.accepting is False


def test_other_building_status_does_not_disclose_edge_identity():
    service = make_service()
    service.start()
    status = service.status("another-building")
    assert status.edge_id is None
    assert status.reason_code == "EDGE_NOT_CONFIGURED_FOR_BUILDING"


def test_simulated_transport_forwards_normalized_envelope_to_existing_ingestion():
    ingestion = FakeIngestion()
    service = make_service(ingestion=ingestion)
    service.start()
    result = service.forward_observation(observation())
    envelope = service.transport.messages[0]
    assert result["forwarded"] is True
    assert envelope.schema_version == "1.0"
    assert envelope.organization_id == ORG and envelope.building_id == BUILDING
    assert envelope.signal == "temperature" and envelope.unit == "°C"
    assert envelope.simulated is True
    assert len(ingestion.observations) == 1
    assert ingestion.observations[0].simulated is True
    assert service.buffer.depth == 0


def test_envelope_is_strict_and_contains_no_credential_fields():
    service = make_service()
    service.start()
    service.forward_observation(observation())
    payload = service.transport.messages[0].model_dump()
    assert {"password", "secret", "token", "credential", "authorization"}.isdisjoint(payload)
    with pytest.raises(Exception):
        EdgeObservationEnvelope(**{**payload, "api_key": "must-not-be-accepted"})


def test_simulated_provenance_mismatch_is_rejected_before_buffering():
    service = make_service()
    service.start()
    result = service.forward_observation(observation(simulated=False))
    assert result["accepted"] is False
    assert result["reason_code"] == "PROVENANCE_MODE_MISMATCH"
    assert service.buffer.depth == 0


@pytest.mark.parametrize("resolved_building,resolved_org", [("building-2", ORG), (BUILDING, "org-2")])
def test_cross_building_or_cross_organization_observation_is_rejected(resolved_building, resolved_org):
    service = make_service(ingestion=FakeIngestion(resolved_building=resolved_building,
                                                    resolved_org=resolved_org))
    service.start()
    result = service.forward_observation(observation())
    assert result["accepted"] is False
    assert result["reason_code"] == "EDGE_OWNERSHIP_MISMATCH"
    assert service.buffer.depth == 0


def test_real_mode_is_explicitly_unavailable_and_buffers_without_claiming_delivery():
    service = make_service(mode=EdgeMode.REAL)
    health = service.start()
    assert health.state == EdgeLifecycleState.DEGRADED
    assert health.transport_state == "UNAVAILABLE"
    assert health.healthy is False and health.simulated is False
    result = service.forward_observation(observation(simulated=False))
    assert result["buffered"] is True and result["forwarded"] is False
    assert service.buffer.depth == 1
    assert service.reason_code == "TRANSPORT_NOT_CONFIGURED"
    assert isinstance(service.transport, UnavailableRealOutboundTransport)


def test_buffer_is_bounded_fifo_and_rejects_newest_explicitly():
    service = make_service(capacity=2)
    service.start()
    service.transport.disconnect()
    service.transport = UnavailableRealOutboundTransport()
    service.transport_state = "UNAVAILABLE"
    first = service.forward_observation(observation())
    first_id = service.buffer.peek()[0].message_id
    second = service.forward_observation(observation())
    second_id = service.buffer.peek()[1].message_id
    third = service.forward_observation(observation())
    queued = service.buffer.peek()
    assert first["buffered"] and second["buffered"]
    assert third["accepted"] is False and third["reason_code"] == "BUFFER_FULL"
    assert [item.message_id for item in queued] == [first_id, second_id]
    assert service.buffer.depth == 2
    assert BoundedObservationBuffer.OVERFLOW_POLICY == "NEWEST_REJECTED"


def test_buffer_acknowledges_only_fifo_prefix():
    service = make_service()
    service.start()
    service.transport.disconnect()
    service.transport = UnavailableRealOutboundTransport()
    service.forward_observation(observation())
    service.forward_observation(observation())
    original = service.buffer.peek()
    assert service.buffer.acknowledge((original[1].message_id,)) == 0
    assert service.buffer.acknowledge((original[0].message_id,)) == 1
    assert service.buffer.peek()[0].message_id == original[1].message_id


def test_ingestion_quality_and_simulated_provenance_are_preserved():
    ingestion = FakeIngestion()
    service = make_service(ingestion=ingestion)
    service.start()
    service.forward_observation(observation(quality=QualityState.VALID))
    envelope = service.transport.messages[0]
    assert envelope.quality == QualityState.VALID.value
    assert ingestion.observations[0].quality_state == QualityState.VALID
    assert envelope.simulated is True


def test_no_control_service_or_hvac_write_capability_is_exposed():
    service = make_service()
    status = service.start()
    assert not hasattr(service, "control_service")
    assert not hasattr(service, "control_provider")
    assert "HVAC_WRITE" not in {capability.value for capability in EdgeCapability}
    assert all(capability != "HVAC_WRITE" for capability in status.capabilities)


def test_bad_observation_contract_is_rejected_by_schema_before_enqueue():
    with pytest.raises(Exception):
        ProviderObservation(integration_id="i", device_id="d", point_mapping_id="p",
            observed_at=datetime.now(), value=float("nan"), source="bad", simulated=True)


def test_simulated_transport_capture_does_not_claim_cloud_connection():
    transport = SimulatedOutboundTransport()
    assert transport.connect() == "SIMULATED_IN_PROCESS"
    assert "CLOUD" not in transport.health()
    transport.disconnect()
    assert transport.health() == "DISCONNECTED"


def test_transport_failure_degrades_without_dropping_then_explicit_flush_recovers():
    class FailsOnceTransport(SimulatedOutboundTransport):
        def __init__(self, receiver):
            super().__init__(receiver)
            self.fail = True

        def send_observation_batch(self, batch):
            if self.fail:
                raise RuntimeError("private transport detail")
            return super().send_observation_batch(batch)

    ingestion = FakeIngestion()
    transport = FailsOnceTransport(None)
    service = make_service(ingestion=ingestion, transport=transport)
    # Bind the simulated receiver to this service's existing ingestion boundary.
    transport.receiver = service._receive_simulated
    service.start()
    result = service.forward_observation(observation())
    assert result["forwarded"] is False and result["buffered"] is True
    assert service.state == EdgeLifecycleState.DEGRADED
    assert service.buffer.depth == 1
    transport.fail = False
    flushed = service.flush()
    assert flushed["forwarded"] is True and flushed["queue_depth"] == 0
    assert service.state == EdgeLifecycleState.RUNNING
    assert len(ingestion.observations) == 1


def test_shutdown_disables_ingress_and_retains_messages_if_transport_unavailable():
    service = make_service(mode=EdgeMode.REAL)
    service.start()
    service.forward_observation(observation(simulated=False))
    stopped = service.stop()
    assert stopped.state == EdgeLifecycleState.STOPPED
    assert stopped.queue_depth == 1
    assert stopped.reason_code == "BUFFER_RETAINED_ON_SHUTDOWN"
    rejected = service.forward_observation(observation(simulated=False))
    assert rejected["accepted"] is False
    assert rejected["reason_code"] == "EDGE_NOT_ACCEPTING"
    assert service.buffer.depth == 1


def test_edge_environment_requires_explicit_building_identity(monkeypatch):
    monkeypatch.delenv("AURATWIN_EDGE_ID", raising=False)
    monkeypatch.delenv("AURATWIN_EDGE_BUILDING_ID", raising=False)
    config, reason = edge_configuration_from_environment()
    assert config is None and reason == "EDGE_IDENTITY_NOT_CONFIGURED"
    monkeypatch.setenv("AURATWIN_EDGE_ID", "test-edge")
    monkeypatch.setenv("AURATWIN_EDGE_BUILDING_ID", "building-1")
    monkeypatch.setenv("AURATWIN_EDGE_MODE", "invalid")
    config, reason = edge_configuration_from_environment()
    assert config is None and reason == "EDGE_MODE_INVALID"


def test_edge_lifecycle_buffer_and_forwarding_emit_existing_event_trace_events():
    EventTrace.clear()
    service = make_service()
    service.start()
    service.forward_observation(observation())
    service.heartbeat()
    service.stop()
    event_types = [event.event_type for event in EventTrace.get_history("edge:edge-test")]
    assert "EDGE_STARTED" in event_types
    assert "EDGE_HEARTBEAT" in event_types
    assert "EDGE_BUFFERED" in event_types
    assert "EDGE_OBSERVATION_FORWARDED" in event_types
    assert "EDGE_STOPPED" in event_types

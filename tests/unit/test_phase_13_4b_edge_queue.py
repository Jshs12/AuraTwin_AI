from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.models import Base, BuildingRecord, EdgeMessageQueueRecord, OrganizationRecord
from backend.edge.queue import DurableObservationQueue
from backend.edge.service import EdgeConnectorService
from backend.edge.transport import SimulatedOutboundTransport, UnavailableRealOutboundTransport
from backend.schemas.edge import (EdgeDeliveryAcknowledgement, EdgeDeliveryStatus,
                                  EdgeConfiguration, EdgeLifecycleState, EdgeMode,
                                  EdgeObservationEnvelope)
from backend.schemas.provider_observation import ProviderObservation


@pytest.fixture
def queue_store():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    org_id, building_id = uuid4(), uuid4()
    with sessions.begin() as session:
        session.add(OrganizationRecord(organization_id=org_id, name="Test", slug=f"test-{org_id.hex}"))
        session.flush()
        session.add(BuildingRecord(building_id=building_id, organization_id=org_id,
            name="Test Building", slug=f"building-{building_id.hex}", building_key=f"key-{building_id.hex}",
            timezone="UTC", address={}))
    yield sessions, str(org_id), str(building_id)
    engine.dispose()


def envelope(org_id: str, building_id: str, *, message_id=None, observed_at=None):
    now = datetime.now(timezone.utc)
    return EdgeObservationEnvelope(message_id=message_id or uuid4(), edge_id="edge-test",
        organization_id=org_id, building_id=building_id, integration_id=str(uuid4()),
        device_id=str(uuid4()), point_id=str(uuid4()), zone_id=str(uuid4()), signal="temperature",
        value=22.5, unit="°C", observation_timestamp=observed_at or now,
        ingestion_timestamp=now, source="SIMULATED_TEST", quality="VALID", simulated=True)


def test_durable_queue_persists_and_preserves_fifo_and_message_uniqueness(queue_store):
    sessions, org_id, building_id = queue_store
    queue = DurableObservationQueue(sessions, edge_id="edge-test", capacity=4)
    first, second = envelope(org_id, building_id), envelope(org_id, building_id)
    assert queue.enqueue(first) and queue.enqueue(second)
    assert queue.enqueue(first)
    assert queue.depth == 2
    reopened = DurableObservationQueue(sessions, edge_id="edge-test", capacity=4)
    assert [item.message_id for item in reopened.peek()] == [first.message_id, second.message_id]


def test_queue_overflow_rejects_newest_and_preserves_oldest(queue_store):
    sessions, org_id, building_id = queue_store
    queue = DurableObservationQueue(sessions, edge_id="edge-test", capacity=1)
    first, second = envelope(org_id, building_id), envelope(org_id, building_id)
    assert queue.enqueue(first)
    assert not queue.enqueue(second)
    assert queue.peek()[0].message_id == first.message_id
    assert queue.metrics()["depth"] == 1


def test_queue_is_edge_scoped_and_rejects_cross_organization_building_owner(queue_store):
    sessions, org_id, building_id = queue_store
    first = DurableObservationQueue(sessions, edge_id="edge-one", capacity=2)
    other_edge = DurableObservationQueue(sessions, edge_id="edge-two", capacity=2)
    item = envelope(org_id, building_id)
    first.enqueue(item)
    assert first.depth == 1 and other_edge.depth == 0
    with pytest.raises(Exception):
        first.enqueue(envelope(str(uuid4()), building_id))


def test_in_flight_crash_recovery_replays_same_message_in_order(queue_store):
    sessions, org_id, building_id = queue_store
    queue = DurableObservationQueue(sessions, edge_id="edge-test", capacity=4, retry_base_seconds=0)
    first, second = envelope(org_id, building_id), envelope(org_id, building_id)
    queue.enqueue(first)
    queue.enqueue(second)
    claimed = queue.claim_batch(limit=1)
    assert claimed[0].message_id == first.message_id
    reopened = DurableObservationQueue(sessions, edge_id="edge-test", capacity=4, retry_base_seconds=0)
    assert reopened.recover_in_flight() == 1
    assert [item.message_id for item in reopened.claim_batch(limit=2)] == [first.message_id, second.message_id]


def test_retry_is_bounded_and_failed_message_is_retained(queue_store):
    sessions, org_id, building_id = queue_store
    queue = DurableObservationQueue(sessions, edge_id="edge-test", capacity=2,
                                    max_attempts=1, retry_base_seconds=0)
    item = envelope(org_id, building_id)
    queue.enqueue(item)
    queue.claim_batch(limit=1)
    assert queue.fail(item.message_id, "SANITIZED_TRANSPORT_ERROR") is False
    with sessions() as session:
        row = session.scalar(select(EdgeMessageQueueRecord))
        assert row.delivery_state == "FAILED" and row.retryable is False
        assert row.last_error_reason == "SANITIZED_TRANSPORT_ERROR"
    assert queue.depth == 1


def test_ack_contract_requires_valid_application_fields_and_duplicate_is_success():
    message_id = uuid4()
    ack = EdgeDeliveryAcknowledgement(message_id=message_id, status=EdgeDeliveryStatus.DUPLICATE,
        server_received_at=datetime.now(timezone.utc))
    assert ack.status == EdgeDeliveryStatus.DUPLICATE
    with pytest.raises(Exception):
        EdgeDeliveryAcknowledgement.model_validate({"message_id": str(message_id), "status": "ACCEPTED"})


def test_real_transport_endpoint_configuration_requires_https_without_embedded_credentials():
    base = {"edge_id": "edge", "building_id": "building", "mode": "real"}
    with pytest.raises(Exception):
        EdgeConfiguration(**base, outbound_endpoint="http://edge.example/observations")
    with pytest.raises(Exception):
        EdgeConfiguration(**base, outbound_endpoint="https://user:password@edge.example/observations")
    assert EdgeConfiguration(**base, outbound_endpoint="https://edge.example/observations").outbound_endpoint


def test_simulated_transport_is_deterministic_and_real_placeholder_is_unavailable():
    transport = SimulatedOutboundTransport(scenarios=["malformed_ack"])
    assert transport.connect() == "SIMULATED_IN_PROCESS"
    real = UnavailableRealOutboundTransport()
    assert real.connect() == "UNAVAILABLE"
    assert real.health() == "UNAVAILABLE"
    transport.disconnect()


class _FakeIngestion:
    def __init__(self, building_id, organization_id):
        self.building_id, self.organization_id = building_id, organization_id
        self.received = []

    def resolve(self, observation):
        return type("Resolved", (), {"building_id": self.building_id,
            "organization_id": self.organization_id, "zone_id": str(uuid4()),
            "signal": type("Signal", (), {"value": "temperature"})(), "unit": "°C"})(), None

    def ingest(self, observation):
        self.received.append(observation)
        return type("Result", (), {"accepted": True, "duplicate": False})()


def test_running_service_uses_durable_queue_and_only_removes_after_simulated_ack(queue_store):
    sessions, org_id, building_id = queue_store
    config = EdgeConfiguration(edge_id="edge-runtime", building_id=building_id,
        expected_organization_id=org_id, mode=EdgeMode.SIMULATED, retry_base_seconds=0)
    configuration = type("ConfigRepo", (), {"get_building": lambda _self, _id: {
        "building_id": building_id, "organization_id": org_id, "archived_at": None}})()
    ingestion = _FakeIngestion(building_id, org_id)
    service = EdgeConnectorService(configuration=configuration, sessions=sessions,
        adapter_registry=type("Registry", (), {"active_items": lambda _self: ()})(),
        ingestion_service=ingestion, config=config)
    assert service.start().state == EdgeLifecycleState.RUNNING
    observation = ProviderObservation(integration_id=str(uuid4()), device_id=str(uuid4()),
        point_mapping_id=str(uuid4()), observed_at=datetime.now(timezone.utc), value=22.0,
        source="SIMULATED_TEST", simulated=True, signal="temperature", unit="°C")
    result = service.forward_observation(observation)
    assert result["forwarded"] is True and service.buffer.depth == 0
    assert len(ingestion.received) == 1
    assert service.status().last_successful_delivery is not None


def test_deterministic_temporary_failure_retries_without_losing_record(queue_store):
    sessions, org_id, building_id = queue_store
    config = EdgeConfiguration(edge_id="edge-runtime", building_id=building_id,
        expected_organization_id=org_id, mode=EdgeMode.SIMULATED, retry_base_seconds=0,
        max_attempts=2)
    configuration = type("ConfigRepo", (), {"get_building": lambda _self, _id: {
        "building_id": building_id, "organization_id": org_id, "archived_at": None}})()
    ingestion = _FakeIngestion(building_id, org_id)
    transport = SimulatedOutboundTransport(scenarios=["temporary_failure"])
    service = EdgeConnectorService(configuration=configuration, sessions=sessions,
        adapter_registry=type("Registry", (), {"active_items": lambda _self: ()})(),
        ingestion_service=ingestion, config=config, transport=transport)
    service.start()
    observation = ProviderObservation(integration_id=str(uuid4()), device_id=str(uuid4()),
        point_mapping_id=str(uuid4()), observed_at=datetime.now(timezone.utc), value=22.0,
        source="SIMULATED_TEST", simulated=True, signal="temperature", unit="°C")
    result = service.forward_observation(observation)
    assert result["buffered"] is True and service.buffer.depth == 1
    flushed = service.flush()
    assert flushed["forwarded"] is True and service.buffer.depth == 0
    assert len(ingestion.received) == 1


def test_malformed_ack_never_deletes_durable_message(queue_store):
    sessions, org_id, building_id = queue_store
    config = EdgeConfiguration(edge_id="edge-malformed", building_id=building_id,
        expected_organization_id=org_id, mode=EdgeMode.SIMULATED, retry_base_seconds=0)
    configuration = type("ConfigRepo", (), {"get_building": lambda _self, _id: {
        "building_id": building_id, "organization_id": org_id, "archived_at": None}})()
    ingestion = _FakeIngestion(building_id, org_id)
    transport = SimulatedOutboundTransport(scenarios=["malformed_ack"])
    service = EdgeConnectorService(configuration=configuration, sessions=sessions,
        adapter_registry=type("Registry", (), {"active_items": lambda _self: ()})(),
        ingestion_service=ingestion, config=config, transport=transport)
    service.start()
    observation = ProviderObservation(integration_id=str(uuid4()), device_id=str(uuid4()),
        point_mapping_id=str(uuid4()), observed_at=datetime.now(timezone.utc), value=22.0,
        source="SIMULATED_TEST", simulated=True, signal="temperature", unit="°C")
    result = service.forward_observation(observation)
    assert result["forwarded"] is False and service.buffer.depth == 1
    assert service.flush()["forwarded"] is True and service.buffer.depth == 0


def test_permanent_transport_failure_is_retained_as_nonretryable(queue_store):
    sessions, org_id, building_id = queue_store
    config = EdgeConfiguration(edge_id="edge-permanent", building_id=building_id,
        expected_organization_id=org_id, mode=EdgeMode.SIMULATED)
    configuration = type("ConfigRepo", (), {"get_building": lambda _self, _id: {
        "building_id": building_id, "organization_id": org_id, "archived_at": None}})()
    transport = SimulatedOutboundTransport(scenarios=["permanent_failure"])
    service = EdgeConnectorService(configuration=configuration, sessions=sessions,
        adapter_registry=type("Registry", (), {"active_items": lambda _self: ()})(),
        ingestion_service=_FakeIngestion(building_id, org_id), config=config, transport=transport)
    service.start()
    observation = ProviderObservation(integration_id=str(uuid4()), device_id=str(uuid4()),
        point_mapping_id=str(uuid4()), observed_at=datetime.now(timezone.utc), value=22.0,
        source="SIMULATED_TEST", simulated=True, signal="temperature", unit="°C")
    result = service.forward_observation(observation)
    assert result["buffered"] is True and service.buffer.depth == 1
    assert service.flush()["forwarded"] is False
    with sessions() as session:
        row = session.scalar(select(EdgeMessageQueueRecord))
        assert row.delivery_state == "FAILED" and row.retryable is False
        message_id = row.message_id
    retried = service.retry_failed(message_id)
    assert retried["accepted"] is True
    assert service.buffer.depth == 0


def test_partial_batch_ack_removes_only_acknowledged_records(queue_store):
    sessions, org_id, building_id = queue_store
    config = EdgeConfiguration(edge_id="edge-partial", building_id=building_id,
        expected_organization_id=org_id, mode=EdgeMode.SIMULATED, retry_base_seconds=0,
        batch_size=2)
    configuration = type("ConfigRepo", (), {"get_building": lambda _self, _id: {
        "building_id": building_id, "organization_id": org_id, "archived_at": None}})()
    ingestion = _FakeIngestion(building_id, org_id)
    transport = SimulatedOutboundTransport(scenarios=["duplicate_ack", "malformed_ack"])
    service = EdgeConnectorService(configuration=configuration, sessions=sessions,
        adapter_registry=type("Registry", (), {"active_items": lambda _self: ()})(),
        ingestion_service=ingestion, config=config, transport=transport)
    service.start()
    def obs():
        return ProviderObservation(integration_id=str(uuid4()), device_id=str(uuid4()),
            point_mapping_id=str(uuid4()), observed_at=datetime.now(timezone.utc), value=22.0,
            source="SIMULATED_TEST", simulated=True, signal="temperature", unit="°C")
    service.buffer.enqueue(EdgeObservationEnvelope(message_id=uuid4(), edge_id=config.edge_id,
        organization_id=org_id, building_id=building_id, integration_id=str(uuid4()),
        device_id=str(uuid4()), point_id=str(uuid4()), zone_id=str(uuid4()), signal="temperature",
        value=22.0, unit="°C", observation_timestamp=datetime.now(timezone.utc),
        ingestion_timestamp=datetime.now(timezone.utc), source="SIMULATED_TEST", quality="VALID", simulated=True))
    second = obs()
    service.forward_observation(second)
    assert service.buffer.depth == 1
    with sessions() as session:
        row = session.scalar(select(EdgeMessageQueueRecord))
        assert row is not None and row.delivery_state == "FAILED"
    assert service.flush()["forwarded"] is True and service.buffer.depth == 0

# Phase 13.4B — Secure Edge Transport and Durable Buffering

## Architecture and scope

The Phase 13.4A Edge Connector now persists validated observation envelopes in the existing SQLAlchemy database through `edge_message_queue`. The default local development database is SQLite. The queue is building/organization scoped, bounded, and contains only the strict scalar `EdgeObservationEnvelope`; it cannot represent commands, HVAC writes, credentials, or camera media.

The path remains:

`read-only adapter → existing observation resolver/quality gate → durable queue → outbound transport → application ACK → queue deletion`

The existing provider mapping resolver and `ProviderObservationIngestionService` remain authoritative. Historical telemetry and ZoneState continue to use their existing services. The edge queue does not create a second telemetry store or update ZoneState directly.

## Message IDs and ordering

An envelope gets one UUID message ID before enqueue. That ID and the immutable serialized envelope survive retries/restarts. The database enforces uniqueness per edge. A monotonically allocated integer queue sequence establishes FIFO insertion order; replay is sequence ordered, and an older ineligible message blocks later messages.

Delivery is **at least once**. The existing telemetry persistence idempotency key deduplicates equivalent observations at ingestion; the receiver reports duplicate ingestion as a `DUPLICATE` ACK. Exactly-once delivery is not claimed. A queue record is deleted only after a valid `ACCEPTED` or `DUPLICATE` application ACK. `REJECTED`, malformed, missing, or mismatched ACKs never count as successful delivery.

## States, retries, and restart recovery

Queue rows use `PENDING`, `IN_FLIGHT`, `DELIVERED`, and `FAILED`. ACKed rows are removed, so `DELIVERED` is reserved by the state contract and is not currently retained. Retryable failures are explicitly marked `FAILED` with a sanitized reason and a next-attempt timestamp. Retry attempts are finite, exponential, capped, and are performed only by a subsequent explicit drain/flush (there is no retry loop or sleep in the request path). Exhausted or rejected records remain durable as non-retryable `FAILED` rows and count against capacity until explicit operator recovery is added.

At startup, any `IN_FLIGHT` records are changed to retryable `FAILED` records with `RECOVERED_AFTER_RESTART`; their immutable envelope and sequence remain. The next flush retries in FIFO order. This is at-least-once recovery: the receiver may already have ingested a message before a process crash, which is why existing idempotent telemetry ingestion is retained. A building-authorized OPERATOR may explicitly re-arm a terminal failed record through `POST /api/edge/status/{building_id}/messages/{message_id}/retry`; the endpoint checks existing assigned-building access and integration-configuration permission, resets that record's bounded attempt budget, and performs one bounded FIFO flush. ADMIN remains oversight-only.

## Capacity and outage behavior

The queue limit is `AURATWIN_EDGE_MAX_BUFFER_MESSAGES`. At capacity, policy `NEWEST_REJECTED` rejects the new observation, emits `EDGE_BUFFER_OVERFLOW`, and preserves queued older messages. Real mode remains `UNAVAILABLE`, so accepted validated observations stay durable while health reports degradation. Flush is explicit and bounded by `AURATWIN_EDGE_BATCH_SIZE`; partial ACKs remove only individually acknowledged records.

Delivery policy configuration is explicit and bounded:

- `AURATWIN_EDGE_MAX_ATTEMPTS`
- `AURATWIN_EDGE_RETRY_BASE_SECONDS`
- `AURATWIN_EDGE_RETRY_MAX_SECONDS`
- `AURATWIN_EDGE_CONNECTION_TIMEOUT_SECONDS`
- `AURATWIN_EDGE_BATCH_SIZE`

The checked-in example values are development/demo policy examples, not production recommendations. The connection timeout is reserved for a future transport; Phase 13.4B makes no real connection.

## Transport and ACK contract

`OutboundTransport` is one-way and supports `connect`, `send_observation_batch`, `acknowledge`, `health`, heartbeat, and `disconnect`. `EdgeDeliveryAcknowledgement` requires a matching message ID, supported schema version, an aware receive timestamp, and `ACCEPTED`, `DUPLICATE`, or `REJECTED` status.

`SimulatedOutboundTransport` invokes the existing ingestion boundary in process and labels ACKs `SIMULATED_IN_PROCESS`. It supports deterministic injected temporary/permanent failures, duplicate/malformed ACKs, timeout, and unavailable scenarios. A simulated ACK proves only local simulated ingestion behavior; it is not cloud delivery.

`UnavailableRealOutboundTransport` remains a placeholder. The optional endpoint must be HTTPS and cannot embed credentials; identity is referenced by configuration, not stored in messages. There is no HTTP client, TLS handshake, certificate configuration, authentication, remote endpoint, or live ACK implementation yet. It always reports `UNAVAILABLE` and does not silently switch to simulation. TLS verification is not disabled because no TLS client exists.

## Security and control isolation

Status remains behind existing authenticated building-read authorization. Payloads, EventTrace, health, and browser status do not include credentials or headers. The only queue schema is an observation envelope; there is no inbound listener, cloud-to-edge message path, control queue, recommendation invocation, `ControlService` dependency, or HVAC write capability. Existing organization/building ownership, confirmed mapping, signal/unit validation, DataQualityGate, and freshness checks execute before enqueue and are rerun by the receiver's existing ingestion path.

## Events and health

EventTrace records queueing, send attempts, ACK/duplicate outcomes, retries/failures, transport state, overflow, and restart recovery with sanitized reason codes. Edge health reports queue depth/capacity, oldest/newest queued observation, last local ACK, last failure, retry count, fullness, delivery status, lifecycle, transport state, and simulated provenance. It does not report a cloud heartbeat or connection as successful.

## Limitations

Phase 13.4B does **not** establish real cloud production connectivity, real BACnet/RTSP/meter connectivity, HVAC control, building energy savings, or production Edge readiness. The queue uses the application's configured database; for a future separately deployed edge, its database must be deployed and operated as local durable storage before making a building-local durability claim. Browser verification was not performed by automated tests.

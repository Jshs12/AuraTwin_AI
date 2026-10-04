# Phase 13.4A — Edge Connector Foundation

## Architecture

Phase 13.4A adds a process-local Edge Connector boundary. It reuses the
existing Phase 13.1 integration/device/point configuration, Phase 13.2
supervised read-only adapter registry, Phase 13.3 observation ingestion,
telemetry persistence, Phase 10 quality/freshness checks, and the existing
ZoneState runtime consumer. It creates no second integration registry,
telemetry store, or runtime-state store.

The explicit orchestration path is:

```text
existing connected read-only adapter
  -> edge scope and mapping checks
  -> versioned scalar observation envelope
  -> bounded FIFO outbox
  -> outbound transport abstraction
  -> existing ProviderObservationIngestionService
  -> existing telemetry persistence / runtime consumer
```

`EdgeConnectorService.poll_integration` is an explicit orchestration call; no
background polling loop or control endpoint is introduced. The standard
integration poll API remains available and retains its existing authorization
and validation behavior.

## Identity and building scope

The process identity is configured with `AURATWIN_EDGE_ID` and
`AURATWIN_EDGE_BUILDING_ID`. The configured building is resolved against the
existing organization/building repository; organization ownership is derived
from that record. `AURATWIN_EDGE_ORGANIZATION_ID`, when supplied, is only an
expected-owner check and must match. For every observation, the existing
ingestion resolver verifies the integration, device, confirmed point mapping,
zone, floor, building, and organization. The edge rejects observations whose
resolved building or organization does not match its bound identity.

The authenticated status route is `GET /api/edge/status/{building_id}`. It
requires `BUILDING_READ` plus existing building authorization. A status request
for a building without this edge returns a scoped `EDGE_NOT_CONFIGURED_FOR_BUILDING`
state without revealing the configured edge identity.

## Lifecycle and application shutdown

The process lifecycle is `STOPPED`, `STARTING`, `RUNNING`, `DEGRADED`,
`STOPPING`, or `ERROR`; it does not replace the existing integration lifecycle.
FastAPI startup starts the configured edge process after reconciling persisted
integration connection states. Shutdown stops accepting observations, makes one
drain attempt where possible, disconnects its transport, then lets the existing
application integration lifecycle close active adapters. The edge does not own
or duplicate adapter connection persistence.

The current heartbeat is process-local and inspectable through edge health. A
simulated heartbeat is captured by the in-process simulator; it is never
reported as delivered to a cloud service.

## Adapter orchestration and capabilities

The edge uses only adapters already present in the supervised adapter registry.
It polls only a configured, connected integration and confirmed readable point
mappings. Any adapter advertising write capability is refused by the edge
orchestration path. Read capabilities in health are reported only for an active
read-only adapter associated with this exact building.

Enabled foundation capabilities are `FORWARD_OBSERVATIONS` and `HEARTBEAT`,
plus protocol read capabilities for eligible active adapters. There is no
`HVAC_WRITE` capability, `ControlService` dependency, HVAC provider dependency,
or control-message type in the edge package.

## Observation envelope and validation

The frozen, extra-forbidden `EdgeObservationEnvelope` contains schema version,
message UUID, edge/organization/building/integration/device/point/zone identity,
logical signal, scalar value, normalized unit, observation timestamp, edge
ingestion timestamp, source, quality state (or `UNASSESSED`), and simulated
provenance. It has no credential, image, video, arbitrary metadata, or command
field. Pydantic rejects malformed/non-finite values and timezone-naive
timestamps before enqueue.

The edge resolves the confirmed mapping and verifies building ownership before
enqueue. In simulated mode, its in-process receiver converts the envelope back
to the existing provider-neutral observation contract and calls
`ProviderObservationIngestionService`. That service re-resolves ownership and
remains authoritative for data quality, freshness, persistence, deduplication,
and runtime-state updates. Historical-only signals remain historical-only.

## Outbound transport and buffering

`OutboundTransport` exposes connect, observation-batch send, heartbeat, health,
and disconnect only. There is no listener, inbound command method, remote shell,
or cloud-to-edge control channel.

`SimulatedOutboundTransport` performs no socket or physical I/O. It captures
messages and heartbeat locally and invokes the existing ingestion service
in-process. Its state is explicitly `SIMULATED_IN_PROCESS`, never “cloud
connected.”

`UnavailableRealOutboundTransport` is a structural placeholder. It opens no
network connection and reports `UNAVAILABLE`/`TRANSPORT_NOT_CONFIGURED`. A
real-mode edge can retain queued messages but cannot claim forwarding.

The in-memory FIFO capacity is configured by
`AURATWIN_EDGE_MAX_BUFFER_MESSAGES` (default 100, bounded to 1–10,000). When
full, the deterministic policy is `NEWEST_REJECTED`; older queued observations
and message IDs remain in order. Overflow emits an event and a safe reason code.
The buffer is process-local and not durable. Production offline persistence
would require a separately reviewed local durable queue.

## Failure and event behavior

Adapter failures are returned as sanitized per-observation results so other
explicit adapter actions can proceed. Cross-building, invalid provenance,
malformed envelopes, and unmapped points are rejected before enqueue. Transport
failures retain queued messages and set degraded health. `flush()` makes one
explicit bounded drain attempt; there is no retry loop. Shutdown also attempts
at most one drain and retains messages if the transport remains unavailable.

The edge reuses the existing in-memory `EventTrace` for events including
`EDGE_STARTED`, `EDGE_STOPPED`, `EDGE_DEGRADED`, `EDGE_ERROR`,
`EDGE_HEARTBEAT`, `EDGE_BUFFERED`, `EDGE_BUFFER_OVERFLOW`,
`EDGE_OBSERVATION_FORWARDED`, `EDGE_OBSERVATION_REJECTED`, and
`EDGE_TRANSPORT_UNAVAILABLE`. No second audit store is added, and event payloads
contain identifiers/reason codes rather than credentials or connection config.

## Credential isolation and security boundary

Existing integration credential references and configuration never enter the
observation envelope. The edge status API is authenticated and scoped to the
requesting user's authorized building. The edge accepts no inbound command API
and exposes no control action. Future deployment must securely provision an
edge identity and a reviewed outbound transport; this phase does not implement
certificate provisioning or credential storage.

## UI and configuration

The Integrations view includes a compact `EDGE FOUNDATION` status card with
building, lifecycle, transport, queue depth, last local heartbeat, last
forwarded observation, simulated provenance, capabilities, and sanitized
reason. Missing identity is presented as unconfigured. UI state does not imply
network/cloud delivery.

Configure the example values in a local environment only when testing the
simulated foundation. Use the existing building key/ID. Do not add these local
values to source or commit a real edge identity. Real mode stays unavailable
until an actual secure outbound transport exists.

## Limitations

Phase 13.4A does **not** establish:

- real BACnet connectivity
- real RTSP connectivity
- real energy-meter connectivity
- production cloud transport
- durable offline buffering
- HVAC control or other control commands
- production edge readiness
- real building energy savings

It provides an explicit process boundary and testable simulated contracts only.

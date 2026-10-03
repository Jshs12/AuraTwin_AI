# Phase 13.1 — Integration and Commissioning Foundation

## Architecture audit and retained boundaries

AuraTwin already had building-scoped SQLAlchemy integration, device and point-mapping records; tenant authorization; operator-controlled mapping states; secret-reference validation; explicit observation ingestion; telemetry persistence; and the existing data-quality gate. This phase extends those records and routes. It does not add a second device repository, telemetry system, runtime state store, or control path.

`ProviderObservationIngestionService` remains the only provider observation path. It checks the integration → device → confirmed readable point → zone → floor → building ownership chain, supported signal/type/unit and quality before telemetry persistence and optional current-state consumption. Rejected/inactive mappings do not feed runtime state. The consumer does not call control. Recommendation, data-quality, freshness, safety, command-limit, manual-override and fail-safe gates remain in their existing order.

## Lifecycle and commissioning states

Connection state is independent from configuration and commissioning approval:

- Connection: `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `DEGRADED`, `ERROR`.
- Configuration remains `CONFIGURED` / `DISABLED`.
- Commissioning states supported by the schema: `CONFIGURED`, `CONNECTION_TEST_PENDING`, `CONNECTION_TESTED`, `DISCOVERY_REVIEW`, `MAPPING_REVIEW`, `READ_ONLY_READY`, `READ_ONLY_MONITORING`, `BLOCKED`, `SIMULATED_COMMISSIONING`.

Current lifecycle behavior is deliberately limited to what existing adapters actually do. Configuration validation writes its timestamp but keeps connection state `DISCONNECTED`; it must never mean connection established. Fixture discovery records `DISCOVERY_REVIEW`; mapping decisions are operator actions. As this repository has no physical adapter registered, populated fixture/manual setup is labeled `SIMULATED_COMMISSIONING`; a configuration field cannot assert physical source capability. `READ_ONLY_READY` is only reachable through the evaluator when an adapter registry positively supplies verified physical source capability, confirmed readable mappings, connected lifecycle state and valid observations. The current registry supplies no such capability, so Phase 13.1 cannot mark anything physically ready.

Lifecycle events are persisted in `integration_lifecycle_events` with transition type, previous/new state, source, simulated provenance and timestamp. The existing audit service also records operator configuration, discovery and mapping actions. A bounded exponential retry policy contract requires explicit caller-supplied attempt and delay bounds. No background connection loop or retry default is introduced.

## Adapter contracts

Typed contracts cover BACnet/IP, RTSP camera and energy meter adapters. They describe connection testing, device/point discovery, observations, health and close/shutdown. Capabilities state read/write metadata separately; BACnet write capability is descriptive only. These are contracts, not implementations: no socket, BACnet, RTSP, camera frame or meter request occurs.

## Discovery and mappings

The simulated fixture provider supplies stable device and point identifiers, protocol, names, unit, data type, readable/writable metadata, capabilities, timestamp, source and fixture quality marker. Repeated fixture discovery upserts by the existing unique integration/device and device/point keys. Fixture records remain `SIMULATED_FIXTURE`; they are never physically discovered.

Suggestions are deterministic and require numeric types, readability, supported signal/unit pairing and explicit zone ownership. Unsupported/uncertain pairs remain `UNMAPPED`. A match is `SUGGESTED`, never `CONFIRMED`; an OPERATOR must confirm. Existing ingestion resolves confirmation and tenant/building ownership again before accepting any observation. Rejected and inactive states remain non-ingestible.

## Health, quality and freshness

The integration health endpoint presents persisted connection state, last observation time, sanitized last error, source, simulated provenance and per-point quality. It reuses `DataQualityGate` and telemetry observation times. Missing data is `MISSING`. Stale is only emitted where existing `DATA_QUALITY_MAX_AGE_*_SECONDS` policy is explicitly configured; no universal age threshold was added. SQLite strips timezone info when reading datetimes, so the integration last-seen comparison treats naive persisted values as UTC, consistent with existing UTC storage.

## Security and control isolation

All routes reuse the existing two-role authorization and building-scope helpers: ADMIN can read/oversee, OPERATOR can configure, validate configuration, request fixture discovery and make mapping decisions only within assigned building scope. Credential references remain boolean-only in responses; secret material is not written to lifecycle events or logs. Physical control is not offered by integration routes. No commissioning state grants control permission, and control continues through the existing ControlService and all Phase 10 safety gates.

## Frontend and API

The existing Integrations UI now shows simulated fixture discovery, connection state, commissioning state, last observation/error, data quality, source and provenance. “Load simulated fixtures” performs a POST but has no network side effect. The configuration check remains explicitly separate from actual connectivity. Commissioning, health and lifecycle are authenticated building-scoped GET endpoints.

## Historical versus runtime observations

Historical telemetry does not update current ZoneState on read. Only an explicit accepted observation through the existing ingestion service can reach the current-state consumer. Supported telemetry point signals remain those of `TelemetrySignal`: occupancy, temperature, power, cumulative energy, cost, tariff rate and cooling setpoint. Only occupancy, temperature and cooling setpoint are eligible as runtime inputs in the current consumer policy; power/energy/tariff/cost remain telemetry and analytics. Cumulative energy is never treated as instantaneous power.

All Phase 13.1 observations are simulated because no physical adapters exist. A `SIMULATED` value is not meter data, a physical sensor reading, verified building readiness, or proof of energy savings.

## Future AuraTwin Edge boundary (design only)

Future deployment may place a building-local Edge gateway near BACnet/IP devices, RTSP cameras and meters. It should isolate protocol credentials locally, expose only authenticated outbound cloud communication, recover connections with bounded backoff, buffer telemetry with explicit limits and timestamps, and keep local operation fail-safe while offline. This phase creates only adapter/deployment contracts; it does not create a gateway, deploy credentials, buffer offline telemetry, or define guaranteed hardware fail-safe behavior.

## Migration and verification

Alembic revision `20261003_09` follows `20261003_08` and adds integration lifecycle metadata plus lifecycle event history. No Supabase connection is assumed. Browser acceptance should inspect the authenticated Integrations view, run configuration validation, load simulated fixtures, assign a zone to a point, observe the suggested mapping, explicitly confirm it and inspect the read-only commissioning/health state. These steps verify software presentation and simulated workflows only; they do not verify physical connectivity or control.

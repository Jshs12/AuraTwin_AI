# Phase 13.3 — Validated Adapter Observation Runtime Integration

## Architecture

Adapter reads use the Phase 13.2 supervised, read-only boundary. An adapter returns a scalar `ProviderObservation` containing integration, device, confirmed point identity, timezone-aware `observed_at`, value, source, simulated provenance, and optional quality and signal/unit hints. The ingestion service resolves organization, building, floor, zone, logical signal, and unit from the persisted integration/device/point hierarchy; adapter-supplied hints are checked against that confirmed mapping and never override it.

The path is:

`read-only adapter → ProviderObservation → confirmed mapping/ownership resolution → unit/domain/DataQualityGate checks → existing TelemetryPersistenceService → explicit existing ZoneStateRuntimeConsumer (critical signals only) → existing dashboards/history`

No adapter observation invokes RecommendationWorkflow, ControlService, or a provider write. The existing `TelemetryObservation` is the canonical normalized tenant-scoped schema. The ingestion response adds the mapping-resolved scope, unit, observation and ingestion timestamps, provenance, quality, and runtime applicability; it is not a second persisted model.

## Ownership and mapping

The resolver follows integration → device → point mapping → zone → floor → building → organization and checks active/configured status, readable numeric point type, zone/floor/building consistency, and tenant hierarchy. Only active `CONFIRMED` mappings are ingestible. `UNMAPPED`, `SUGGESTED`, `REJECTED`, `INACTIVE`, orphaned, cross-building, or invalid ownership chains fail closed. Discovery remains advisory and does not confirm a mapping.

The browser-facing integration APIs retain their existing building authorization checks. Provider observations cannot supply trusted tenant or zone ownership; those identifiers are resolved server-side.

## Signal, unit, quality, and freshness

Units are validated against the existing signal rules: occupancy counts, Celsius temperature/setpoint, kW power, kWh cumulative energy, ISO currency cost, and currency/kWh tariff. No unit conversion is inferred. Domain and range checks reuse the existing `DataQualityGate`; upstream `STALE`, `MISSING`, `INVALID`, or `OUT_OF_RANGE` quality is preserved and rejected for persistence/runtime application. Configured freshness and future-time policies remain the sole freshness thresholds; Phase 13.3 introduces no production defaults.

Only explicitly runtime-requested occupancy, temperature, and cooling-setpoint observations can update current state. Adapter poll enables this only for those mapped signals; simulated manual observations use the same provider-neutral path. Power, energy, cost, and tariff remain historical telemetry. Repeated observations obey telemetry idempotency and duplicates do not reapply runtime state. Runtime state rejects observations whose timestamps are not strictly newer than the current runtime observation for that signal. This prevents an older, otherwise policy-fresh observation from overwriting newer state.

`observed_at` is never replaced by read/ingestion time. Telemetry persistence retains its existing database `ingested_at`; the response and latest-observation UI expose both timestamps. Cumulative-energy attribution remains in the existing optimization interval subsystem and its boundaries/provenance rules are unchanged.

## Provenance and supported adapter behavior

`simulated` and `source` are preserved in persisted telemetry, runtime state, the ingestion response, events, audit summaries, and UI. Adapter-reported provenance must agree with adapter health. A provider cannot label an observation non-simulated unless the active adapter also reports non-simulated health and physical I/O capability. Fixture and manual observations remain explicitly simulated.

- **Camera:** only a derived mapped occupancy scalar can enter ingestion; image/video payloads are forbidden and no video is retained. The current physical RTSP adapter is unavailable. Simulated occupancy still follows the same mapping, quality, persistence, and runtime path.
- **Energy meter:** mapped power, cumulative energy, and tariff observations can be persisted as history when provided. AuraTwin does not fabricate readings, convert power to energy, or claim savings from these observations.
- **BACnet:** Phase 13.2 adapter contracts are read-only. Temperature, cooling setpoint, occupancy, and appropriate meter signals can be represented by confirmed mappings. No BACnet write or actuator operation is introduced.
- **Physical connectivity:** the shipped BACnet/IP, RTSP, and meter adapters remain explicit unavailable placeholders. No physical observation was produced or verified by this phase.

## Persistence, health, events, and UI

Accepted observations reuse tenant-scoped telemetry persistence and its existing idempotency key. The existing bounded `AuditService` holds sanitized latest-attempt summaries (signal, outcome, quality, timestamps, source/provenance, and reason); it stores no credentials, headers, or raw adapter configuration. Integration health reports observation status (`NO_VALID_OBSERVATIONS`, `VALID`, `STALE`, or `REJECTED`) and the latest attempt. Existing `EventTrace` emits `OBSERVATION_RECEIVED` followed by `OBSERVATION_ACCEPTED` or a deterministic rejection event such as `MAPPING_REJECTED`, `STALE_OBSERVATION`, or `INVALID_OBSERVATION`.

The Integrations screen shows poll results, value/unit, observation and ingestion timestamps, quality, source/provenance, mapping, runtime applicability/result, and sanitized reason code. Persisted point history remains distinct from runtime applicability. Runtime values appear in the existing zone state only after all validation passes.

## Limitations and control isolation

This phase adds no recurring poll loop; polling remains explicit and bounded. Audit outcome summaries use the existing bounded in-memory audit service and are not durable across process restart; persisted accepted telemetry remains durable under the existing database configuration. The physical protocol adapters are not configured, and browser acceptance requires an authenticated running application; tests alone are not browser acceptance.

**Phase 13.3 does NOT establish physical HVAC control.** Adapter observations do not call control or recommendation services and cannot issue HVAC writes. Simulated state and telemetry remain explicitly simulated and do not prove building operation, meter readings, or energy savings.

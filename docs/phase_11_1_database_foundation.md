# Phase 11.1 — Database Foundation and Architecture Audit

> Historical Phase 11.1 audit. Runtime persistence/auth/configuration cutover is
> implemented in Phase 11.2; see `phase_11_2_persistent_configuration.md` for
> the current application behavior.

## Audit scope and current application shape

AuraTwin is a FastAPI application assembled in `backend/api/main.py`. That
module constructs providers and application services at import time and
registers the monitoring router. There is no general dependency-injection
container, application repository layer, SQL engine, or migration history in
the pre-Phase 11 application.

The main flows are:

- **Authentication and authorization:** `AuthService` uses `UserRepository`
  and Argon2/JWT helpers. The app currently injects `InMemoryUserRepository`;
  the `ADMIN` and `OPERATOR` role permissions are defined in
  `backend/security/roles.py`. `BuildingAccessRepository` checks the
  operator's assigned building ids. Zone-to-building resolution is currently
  the development-building constant and must be replaced with persisted
  ownership before multi-building API rollout.
- **Audit:** `AuditService` retains up to 2,000 records in a deque.
- **Building/zone configuration:** `ZoneStateService` loads zones from
  `data/building/zones.json`; providers supply changing occupancy, temperature,
  energy, tariff, and HVAC state. It computes the quality report.
- **Recommendations and control:** `RecommendationWorkflow` validates model
  or deterministic fallback recommendations. `ControlService` rechecks
  freshness, TTL, safety, command limits, readiness, and the Phase 10.4
  per-zone mode gate immediately before a simulated provider write.
- **Integrations/providers:** abstract provider interfaces separate occupancy,
  temperature, energy, tariff, and building control from mock, YOLO, and
  simulated BACnet implementations. The BACnet-named implementation is a
  simulator, not a network integration.
- **Monitoring/events:** an asyncio scheduler owns its task and per-zone
  transient status; `EventTrace` keeps a process-local bounded event list and
  `EventBroadcaster` manages process-local WebSocket subscribers.
- **Demo/energy:** `DemoScenarioEngine` owns scenario playback state and a
  task. `BuildingEnergyTelemetry` calculates a rolling, simulated energy
  estimate from zone state.
- **Camera/occupancy:** camera providers produce frames for YOLO inference.
  Detection returns occupancy metadata and an annotated local image path; the
  ordinary occupancy decision path does not need permanent raw-frame storage.

## In-memory state classification

| State/data | Classification | Persistence decision |
| --- | --- | --- |
| User identities, password hashes, active state | Must persist | The schema has `users`; passwords are stored only as hashes. |
| Organization membership and operator-to-building assignments | Must persist | Separate `organization_memberships` and `user_building_access` tables; role and scope are distinct. |
| Organization/building/floor/zone definitions and comfort/capacity/area configuration | Must persist | Normalized configuration entities are created in this phase. |
| Integration/device/point configuration | Must persist | Foundation tables are created; secrets are references, not inline credentials. |
| Audit records and security/access changes | Must persist (append-only) | Keep in memory for this foundation; wire an append-only audit repository in a later subphase. |
| Manual override, control-enable, and provider-failure latch | Must persist before production control | Current mode state is in memory and app startup is disabled. Durable fail-safe policy is explicitly deferred until a production control integration exists. |
| Occupancy observations and data-quality assessments | Historical/append-only | Persist privacy-minimized occupancy metadata in a later telemetry subphase; assessments are derived from source observations and configured policy. |
| Temperature and energy readings, tariffs/cost snapshots | Historical/append-only | Defer until a telemetry ingestion/retention boundary is defined. Current energy stream is simulated. |
| Recommendations, validation outcomes, control commands/results | Historical/append-only | Defer until service-level command/recommendation repositories and retention semantics exist. |
| Operational events | Historical/append-only | Defer persistence until event schema, ordering, retention, and delivery semantics are defined. Current WebSocket event history is bounded. |
| Current `ZoneState` and `ZoneDataQualityReport` | Derived/cache | Rebuild from provider/sensor observations and persisted configuration; do not treat reads as observations. |
| Monitoring scheduler status, asyncio tasks, scene detector frames, WebSocket subscribers | Runtime-only | Process resources, not durable domain state. |
| Demo scenario playback, scenario phase, simulated cumulative energy | Runtime-only / derived | Resettable demo state; no production source of truth. |
| Raw camera frames and annotated debug images | Runtime-only / optional debug artifact | No normal-workflow cloud/object storage or snapshot table is created. |

## Persistence boundary

The intended boundary is `FastAPI -> application services -> repository
protocols -> SQLAlchemy adapters -> PostgreSQL`. Domain/auth code should call
repository methods and should not issue SQL itself. At the end of Phase 11.1,
the running FastAPI app continued to use in-memory stores. Phase 11.2 has since
completed the local SQLite app wiring and repository-backed configuration/auth
cutover while retaining runtime-only ZoneState, telemetry, and events.

At the end of Phase 11.1, database configuration was opt-in. The Phase 11.2
local application now defaults to `data/auratwin.db`; the settings object itself
still permits no URL for isolated foundation tests. The URL is excluded from
configuration representations and must not be logged.

## Entity model

`organizations 1:N buildings 1:N floors 1:N zones` models the setup hierarchy.
`users N:M organizations` is represented by `organization_memberships`, while
`users N:M buildings` is explicit in `user_building_access`. Both associations
are separate from the global two-value role. The current application does not
yet use persisted organization membership in authorization: its ADMIN access
remains global as implemented, and OPERATOR access remains assignment-based.
The Phase 11.2 cutover must explicitly connect the repository-backed
organization/building scope to authorization without broadening permissions.

`buildings 1:N integrations 1:N devices 1:N point_mappings` keeps an
integration, a physical/logical device, and a protocol point separate. Devices
may be assigned to a zone. Each point mapping names an AuraTwin logical signal
such as `temperature`, `cooling_setpoint`, `fan_status`, `occupancy`, `power`,
or `energy`. Integration type and configuration remain provider-neutral.
Credential material is not stored in `configuration`; only a future secret
reference is represented.

The schema supports arbitrary numbers of organizations, buildings, floors,
zones, integrations, devices, and points. The four current demo zone ids remain
valid identifiers at the application layer; they are not seeded or hard-coded
as database rows by this migration.

## Database and migration choice

SQLAlchemy 2.x provides one PostgreSQL-capable model/adapter layer and supports
SQLite for isolated local repository tests. Alembic manages versioned schema
changes. PostgreSQL UUID, JSON/JSONB, standard timestamp, foreign-key, unique,
and check constraints are used without Supabase-only extensions. The initial
migration can be exercised on local PostgreSQL or SQLite; tests use SQLite
memory and do not require an external service.

Supabase is the planned managed PostgreSQL host, but this phase neither
connects to Supabase nor creates a project schema. Supply a PostgreSQL
SQLAlchemy URL through `DATABASE_URL` later. Credentials must stay outside
source control and logs.

## Camera privacy

**Raw camera snapshots are not persisted by the standard occupancy pipeline.**
The standard flow is frame capture, person inference, occupancy metadata, and
release of the frame from the processing path. No object storage, image bucket,
or snapshot table is part of Phase 11.1. Any future opt-in debugging/evidence
capture needs a separate retention, access, and privacy design.

## Historical data boundary and deferred work

This phase intentionally does not add occupancy, temperature, energy, tariff,
recommendation, control-result, event, or audit history tables. Their current
sources are providers, bounded queues, and service-local event flows; persisting
them now would create competing write paths and unclear transaction/retention
semantics. They should be added after each corresponding service has a
repository interface and a defined source-of-truth, idempotency, retention,
and tenant/access policy. Audit persistence should be prioritized before
production use because the current audit deque is process-local.

Also not implemented as of Phase 11.1: live Supabase connection, schema
deployment, onboarding UI, real BACnet, camera image storage, telemetry history,
or historical data persistence. Phase 11.2 has since added local database
startup wiring, multi-building configuration routes, and the idempotent import
of legacy demo zones.

## Next Phase 11 steps

1. Add privacy-minimized occupancy and time-series telemetry persistence with
   retention/partitioning policies.
2. Add recommendation/control/event history and durable operational-state
   design before any real control integration.
3. Only then configure a Supabase PostgreSQL project and deploy reviewed
   migrations.

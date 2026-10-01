# Phase 11.2 — Persistent organization and building configuration

## Source of truth and persistence boundary

The local application now initializes the Phase 11.1 Alembic schema and uses
SQLAlchemy repositories for authentication users, organization membership,
building access, and organization/building/floor/zone configuration. SQLite is
the local default at `data/auratwin.db` when `DATABASE_URL` is unset. Set
`DATABASE_URL` to an approved SQLAlchemy URL to select another database; a
PostgreSQL database is not automatically migrated or bootstrapped and must be
migrated explicitly. Keep credentials out of source and logs.

Persistent configuration is the source of truth for organization, user,
membership, building access, building, floor, and zone definitions. The runtime
rebuilds its zone configuration from active database records. `ZoneState`, live
provider readings, recommendations, telemetry, events, audit history, and demo
playback remain runtime/derived data; this phase adds no history tables or
Supabase connection.

## Hierarchy and bootstrap

The configuration model is:

```text
Organization
├── users and memberships
└── buildings
    └── floors
        └── zones
```

On local SQLite startup, migrations are applied and the checked-in
`data/building/zones.json` is imported idempotently into a deterministic AuraTwin
development organization, building, and ground floor. All ten existing zone
IDs, capacity, area, type, and comfort ranges are preserved. Existing records
are not overwritten; subsequent operator edits remain authoritative. Other
database backends require explicit migration and are not silently seeded.

The ten demo zones and the four-zone `DemoScenarioEngine` are demo configuration,
not required architecture defaults. New buildings may have no zones or any
number of zones. Runtime monitoring/status counts use active persistent zones.

## Access model

Roles remain exactly `ADMIN` and `OPERATOR`.

- `ADMIN` retains the Phase 9 oversight-only permissions: read access to
  organizational/building resources and audit/system views. ADMIN cannot
  configure buildings/zones or perform operational control.
- `OPERATOR` receives configuration and operational permissions only for
  explicitly assigned buildings. Organization membership does not implicitly
  grant access to every building in that organization.
- User accounts, password hashes, organization memberships, and building
  assignments are stored through the database-backed user repository. Password
  hashes remain Argon2 hashes; login continues to issue JWTs.
- Zone APIs resolve each zone's persisted floor/building and apply building
  access before the operation. Monitoring selects the operator's authorized
  zones, filters status/events to those zones, and prevents another operator
  from taking over or stopping a running scope.

## API and runtime flow

The authenticated configuration API supports organization read, authorized
building list/read/create/update/archive, floor list/read/create/update/archive,
and zone list/read/create/update/archive. Archive is a soft lifecycle change;
the underlying rows and relationships remain available to historical references.
Building and zone runtime changes refresh the in-memory registry. The existing
`/api/zones` and `/api/zones/{zone_id}` routes remain available, with optional
`building_id` filtering on the zone list. Legacy demo IDs remain accepted, while
new zones use their persistent UUIDs.

The frontend includes a minimal selector when the authenticated user can access
multiple buildings. The dashboard requests zones for the selected building.
Full onboarding forms are deferred.

All recommendation, data-quality/freshness, safety, command-limit, fail-safe,
manual-override, and provider-write checks remain in their existing services.
Persistent configuration does not make the simulated BACnet provider real
hardware and does not establish real sensor freshness or building safety.

## Camera privacy and deferred work

The occupancy pipeline remains frame capture, inference, occupancy metadata, and
frame release. Phase 11.2 adds no camera-image database, object storage, or
retention change.

Not implemented in this phase: Supabase connectivity/deployment, telemetry or
occupancy history, recommendation/control history, persistent audit history,
integration/device onboarding, real BACnet, real cameras, or complete building
onboarding UI. These remain later Phase 11 work, including Phase 11.3's defined
historical telemetry persistence boundary.

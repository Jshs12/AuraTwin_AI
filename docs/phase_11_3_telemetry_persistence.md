# Phase 11.3 — Tenant-scoped telemetry persistence

AuraTwin stores privacy-minimized scalar observations in the PostgreSQL-compatible
`telemetry_observations` table. Each record has organization, building, floor,
and database zone UUIDs; a signal, value, and unit; source observation time;
database ingestion time; and nullable source, quality state, and simulation
provenance. Composite foreign keys enforce the organization → building → floor
→ zone lineage. Application queries additionally scope every result by the
resolved organization and building.

## Signals and time

The persisted signals are occupancy (people), temperature (°C), power (kW),
energy (kWh), cost (the tariff currency), and tariff rate (currency/kWh).
`observed_at` is copied only from a provider/domain observation timestamp;
legacy generated/read timestamps are not substituted. Signals without an
observation timestamp are skipped. `ingested_at` is assigned by the database
and is never treated as sensor freshness.

Source and `simulated` values are preserved when present. A source value of
`unknown` becomes SQL NULL, and absent values stay NULL. Quality is copied from
the Phase 10 report when that signal has an assessment; no quality is inferred
by persistence. Simulated observations remain explicitly marked simulated.

## Idempotency and retention

The idempotency key is SHA-256 over organization UUID, building UUID, zone UUID,
signal, exact observation timestamp, and source. A missing source has a distinct
sentinel in the hash but remains NULL in storage. A repeated key does not insert
another row. A conflicting payload with an identical scope/signal/time/source
keeps the first persisted row; persistence does not overwrite history.

`TELEMETRY_RETENTION_DAYS` is optional and has no default duration. Unset means
retention cleanup is disabled. If configured, it must be a positive integer.
Cleanup is explicit through the telemetry service/repository and is never run
during application startup.

## Privacy and API access

The standard occupancy pipeline stores derived occupancy metadata, not raw
camera snapshots, frames, annotated images, video, face images, or biometric
data. The persistence model accepts scalar signal values only; it does not
store `ZoneState` dumps or event payloads. Existing in-memory event and demo
energy windows remain runtime/demo features; their generated display timestamps
are not used as observation timestamps.

Zone and building history endpoints resolve tenant lineage server-side; clients
cannot select an organization ID. Existing zone/building authorization is
checked before querying. Operators remain restricted to assigned buildings;
admins retain oversight access. No frontend analytics/history screen was added.

No recommendation, control command, audit history, image, or video persistence
is included. Supabase and real BACnet/hardware remain deferred.

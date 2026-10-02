# Phase 12.3 — Persistent optimization intervals and energy attribution

Optimization intervals now use the existing SQLAlchemy database and Alembic migration system. Each row has organization, building, floor, and database zone ownership, while retaining the runtime zone key used by existing API/UI paths. Composite foreign keys preserve the existing ownership chain. A partial unique index permits at most one active interval per zone.

The existing monitoring scheduler and `OptimizationIntervalService` remain the lifecycle path. The interval is created only after a successful control result reports an applied setpoint. A fresh post-write `ZoneState` snapshot is used for provenance/boundaries. Recovered active intervals are read from storage and continue to suppress repeated identical occupancy decisions; recovery does not issue a command. The existing workflow, data-quality/freshness checks, safety validation, command limits, fail-safe/manual override, and provider readiness gates remain responsible for any later control.

## Telemetry attribution

Attribution uses only the existing tenant-scoped persisted telemetry repository. Cumulative `energy` values are kWh; `power` values are kW and are never integrated or subtracted here. A boundary must match the runtime observation by zone, value, unit, timestamp, source, quality, and simulated provenance. The start observation must not predate the successful control timestamp. End observations must be later than their corresponding start observations, have valid quality, and retain compatible unit and provenance. A decreasing cumulative reading is invalid.

Energy consumed is the nonnegative difference between validated cumulative-energy boundaries. If either boundary or a compatibility check is missing, the interval records an unavailable/invalid status and stable reason code; no value is fabricated. Cost is calculated only when energy is valid and tariff boundaries and all persisted tariff observations during the interval establish one unchanged, valid rate with compatible unit/source/provenance. More than 500 tariff rows in an interval is conservatively treated as unverifiable. Rate changes are not segmented because the existing data does not align energy deltas to tariff segments; cost remains unavailable.

No savings calculation is implemented. Consumption during an optimization interval is not evidence of savings. The UI states that savings are not yet measurable because there is no validated comparison baseline. Simulated provenance remains visible; a simulated calculation is not a meter reading or real-building result.

## API and access

The existing authenticated `GET /api/zones/{zone_id}/optimization-intervals` endpoint returns active and recent completed intervals and identifies database persistence. It retains the existing zone read authorization dependency, so existing ADMIN/OPERATOR building rules apply. No new authorization path or telemetry store is introduced.

## Events and limitations

Existing lifecycle events are retained: `OPTIMIZATION_STARTED`, `OPTIMIZATION_HOLDING`, `OCCUPANCY_CHANGED`, `OPTIMIZATION_COMPLETED`, energy/cost impact availability events, and `SAVINGS_BASELINE_UNAVAILABLE`. Event payloads contain interval identifiers and safe attribution values/reasons only.

The local SQLite runtime applies the migration at startup through the existing migration mechanism. Other database deployments must apply Alembic migrations before use. Intervals are persisted, but runtime sensor state and event history retain their existing lifecycle. Attribution is unavailable when the current provider cannot supply fresh, persisted, post-control cumulative energy and compatible tariff observations. This phase does not add real BACnet, meter integration, or production savings claims.

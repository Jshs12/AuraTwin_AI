# Phase 11.4 — Authorized historical telemetry analytics

## Query API and authorization

Historical points are exposed by authenticated REST endpoints:

- `GET /api/telemetry/latest?zone_id=...&signal=...` — latest point for a zone, optionally one signal.
- `GET /api/zones/{zone_id}/telemetry/latest?signal=...` — equivalent scoped-zone form.
- `GET /api/zones/{zone_id}/telemetry` — zone history.
- `GET /api/floors/{floor_id}/telemetry` — floor history, optionally narrowed by zone.
- `GET /api/telemetry/buildings/{building_id}` — building history, optionally narrowed by floor and zone.
- `GET /api/buildings/{building_id}/telemetry` — backward-compatible building history path.

Historical endpoints require timezone-aware `start_time` and `end_time` (the
older `start_at`/`end_at` spellings remain accepted on zone/building routes).
Start must precede end; future end times are rejected. Results are ordered by
observation time, newest first. The response contains typed scalar observation
points, database-side per-signal `count`, `minimum`, `maximum`, and `average`,
resolved query filters, and a truncation indicator. No organization ID is
accepted from the client. Organization and building scope are resolved from
stored zone/floor/building relationships, and existing `ZONES_READ` permission
and building access checks are applied. Operators see only assigned buildings;
ADMIN follows the existing Phase 9 oversight authorization behavior.

`TELEMETRY_QUERY_MAX_LIMIT` controls the maximum returned point count; its
current default is 500 and invalid/non-positive configuration is rejected.
This bound limits returned points. Aggregates are evaluated by the database over
all rows matching the explicit scope, signal, and time range. The frontend
offers 24-hour, 7-day, and 30-day ranges, but the API accepts explicit ranges.

## Aggregation semantics

The supported summaries are count, min, max, and average, computed in SQL. No
sum is exposed. Energy observations come from `EnergyReading.energy_kwh`, an
accumulated/cumulative value in the current application; summing these readings
would double-count consumption. Cost is also estimated cumulative cost. Power,
temperature, occupancy count, and tariff-rate point summaries use their
observation samples. No interval energy or savings estimate is derived.

## Dashboard and data distinction

The Energy view contains the existing WebSocket/runtime energy stream and a
separate Historical Telemetry panel that reads the persisted REST API. The
building selector already used by the dashboard drives the historical request;
the panel then filters floors, zones, signals, and time range. Historical charts
render individual time-positioned points without joining gaps with a line.
Loading, error, empty, simulation, and unknown simulation provenance states are
shown explicitly. Latest/min/max/average summaries reflect matching records;
the plotted point set respects the API limit.

Historical records preserve source, quality, simulation flag, unit, observation
time, and ingestion time where stored. A provider marking a point non-simulated
does not establish sensor accuracy. Occupancy visualization is count metadata
only. AuraTwin stores derived occupancy metadata, not raw camera snapshots, as
part of its telemetry pipeline. The existing monitoring WebSocket continues to
carry live runtime events and was not replaced with historical data.

No real sensor accuracy or building energy savings are established. This phase
does not add recommendation/control/audit history, image/video storage,
forecasting, Supabase connectivity, or hardware control.

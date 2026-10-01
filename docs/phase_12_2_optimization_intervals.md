# Phase 12.2 — Optimization intervals

## Runtime flow

The existing `RecommendationWorkflow` and `ControlService` remain the only recommendation and command path. After a recommendation passes all current gates and a simulated provider confirms a changed setpoint, `OptimizationIntervalService` starts a zone interval. It records the pre-write occupancy/temperature/setpoint and the applied setpoint. It does not issue commands itself.

While the observed occupancy count stays equal to the interval's starting count, monitoring and the demo scheduler hold the validated target and skip another recommendation/write. In the demo, the existing HVAC simulator may advance thermal and energy telemetry during this hold. A changed count closes the interval and allows the existing workflow to evaluate the new state; quality, freshness, safety, limits, control mode, and provider readiness still apply.

The boundary is exact count inequality, but only an occupancy assessment already marked `VALID` can close an interval. Missing, stale, invalid, or out-of-range occupancy continues the hold; the existing workflow independently blocks recommendations when critical inputs fail. This is a deterministic demo/runtime policy, not a universal building policy and not a configured production threshold. This phase adds no occupancy debounce or percentage threshold.

## Energy and cost accounting

The interval reports cumulative **energy consumed** (kWh) as the non-negative difference between two cumulative-energy observations. It never calls this difference savings or an energy impact attributed to optimization. Cost consumed is that kWh difference multiplied by the same valid tariff rate and currency at both boundaries.

Both boundary energy and tariff quality assessments must be `VALID`; observations must have timestamps; the ending cumulative reading must not precede the starting reading; source/simulation provenance and tariff rate/currency must match. Otherwise the interval leaves energy and cost blank and emits `ENERGY_IMPACT_UNAVAILABLE`. Power (kW) is not integrated or treated as cumulative energy here. Simulated telemetry stays marked `SIMULATED` and is not meter data.

## State and API

Intervals live in a process-local `OptimizationIntervalService`, consistent with the existing runtime monitoring/control mode lifecycle. History is bounded to the latest 100 completed intervals per zone and is lost on process restart. It is not reconstructed from historical telemetry. The authorized read endpoint is `GET /api/zones/{zone_id}/optimization-intervals`; it enforces the existing zone read permission/building boundary and returns `persistence: PROCESS_LOCAL`.

The zone detail and building overview display active holds. Zone detail shows recent completed intervals and only shows energy/cost values when both validated boundary readings exist. No database table or Alembic migration is added in this phase.

## Safety and limitations

An interval is started only after `ControlService` returns a successful applied command with a changed setpoint. The interval service does not validate or bypass control: data quality/freshness, recommendation TTL, safety constraints, command limits, fail-safe/manual override, authorization and provider readiness remain authoritative. Failed or rejected writes do not start an interval.

The current deterministic simulator may update simulated power in response to HVAC state and occupancy. That illustrative model is not building physics, real HVAC operation, meter-verified energy, or evidence of savings. If control remains locked because command-limit configuration is incomplete, the software must remain locked; an optimization interval will not be manufactured to make the demo appear active.

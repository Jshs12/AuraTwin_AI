# Phase 10.2: stale-data protection

AuraTwin's Phase 10.1 `DataQualityGate` remains the single classifier for signal
quality. It reports `VALID`, `STALE`, `MISSING`, `INVALID`, or `OUT_OF_RANGE`.
Phase 10.2 adds a final independent quality and safety recheck at the simulated
control provider boundary.

## Freshness policy

Freshness age limits are opt-in configuration. AuraTwin does not choose a
production sensor threshold. Configure only values approved for the relevant
signal, sensor, and deployment:

| Critical signal | Configuration | Quality report key |
| --- | --- | --- |
| Occupancy observation | `DATA_QUALITY_MAX_AGE_OCCUPANCY_SECONDS` | `occupancy` |
| Temperature observation | `DATA_QUALITY_MAX_AGE_TEMPERATURE_SECONDS` | `temperature` |
| Current HVAC setpoint observation | `DATA_QUALITY_MAX_AGE_SETPOINT_SECONDS` | `hvac_setpoint` |

An unset age limit does not classify an old, otherwise well-formed timestamp as
`STALE`; this preserves Phase 10.1 behavior. A missing observation timestamp is
still `MISSING`, and a timestamp beyond the configured future clock-skew
tolerance is `INVALID`. The existing
`DATA_QUALITY_FUTURE_CLOCK_SKEW_SECONDS` defaults to zero. Energy, tariff, and
energy-cost ages can be reported, but those values do not independently block
control.

Do not use a read time, API response time, or generated event timestamp as a
substitute for observation time. Simulated providers label their observations
as simulated; their timestamps describe the simulator's state, not real sensor
data. Legacy providers without explicit observation timestamps remain
`MISSING` for required critical signals.

## Enforcement points

1. `ZoneStateService` builds provenance-aware assessments.
2. `RecommendationWorkflow` checks occupancy, temperature, and current HVAC
   setpoint before invoking intelligence. It also checks fallback inputs
   independently.
3. `ControlService` refreshes/rechecks critical inputs and revalidates the
   recommendation before control. Phase 10.2 repeats this check at the last
   provider-write boundary so a signal that ages during the control path cannot
   proceed to `write_command`.
4. The autonomous monitoring and demo flows use the same recommendation and
   control services; a rejected quality assessment does not authorize control.

`RECOMMENDATION_TTL_SECONDS` is a separate age limit for the recommendation
itself. It does not replace signal freshness limits and does not make sensor
data fresh. Existing `SafetyConstraintService` checks remain authoritative.

## Phase 10.3 command limits

The existing `SafetyConstraintService` also owns the command limit policy.
Configure all three values from an approved policy before enabling control:

| Configuration | Meaning |
| --- | --- |
| `COMMAND_LIMIT_MIN_SETPOINT` | Inclusive absolute minimum setpoint in °C |
| `COMMAND_LIMIT_MAX_SETPOINT` | Inclusive absolute maximum setpoint in °C |
| `COMMAND_LIMIT_MAX_DELTA` | Maximum absolute change from the latest HVAC setpoint, in °C |

There are no production defaults. Missing, non-finite, or internally
inconsistent values reject recommendations and commands. The limits apply to
provider recommendations and deterministic fallback recommendations. At the
last control boundary, `ControlService` validates the exact command against
the latest `ZoneState`, including zone, supported action, absolute limits,
per-command delta, current setpoint, and zone comfort range. The simulated
BACnet provider repeats the command-value checks as a defense at its own write
method. A rejected command does not reach a provider write from `ControlService`.

The pytest environment sets explicit test-only values (16, 30, and 2 °C) to
exercise configured behavior; these are not application defaults or
recommended building limits. Configure production values only through the
deployment environment and an approved control policy.

## Limits

This protects the software decision path using supplied timestamps and
configured age limits. It does not establish the accuracy, clock quality, or
physical freshness of real sensors. The current BACnet-named provider is a
simulator; there is no real BACnet, RTSP, or production sensor integration in
this phase.

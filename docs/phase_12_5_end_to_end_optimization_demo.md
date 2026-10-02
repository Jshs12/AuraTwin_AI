# Phase 12.5 — End-to-end optimization demo validation

## Existing path reused

The existing authenticated Demo Mode and `DemoScenarioEngine` remain the
runner. It emits deterministic occupancy observations for its four configured
demo zones through `ZoneMonitoringScheduler.process_simulated_occupancy`.
That scheduler continues to use `ZoneStateService`, `RecommendationWorkflow`,
`SafetyConstraintService`, `ControlService`, the simulated BACnet-ready
provider, `OptimizationIntervalService`, and the existing tenant-scoped
telemetry and optimization repositories. No alternate runtime state or
telemetry store was added.

## Demo-only safety profile

The ordinary command-limit policy stays fail-closed and unchanged. When an
authorized OPERATOR starts Demo Mode, the backend first requires the existing
control provider to report both simulated provenance and readiness. It then
constructs `SIMULATED_COMFORT_BOUNDED` for the scheduler's demo-only workflow
and control service. Absolute bounds come from the configured comfort ranges
of the demo zones; the maximum per-command delta is the narrowest configured
demo-zone comfort-band width. Each recommendation is still checked against
the individual zone comfort range, data quality/freshness, recommendation
validation, command limits, provider readiness, manual override, and fail-safe
state. The profile is not installed on the normal API workflow or control
service and cannot be constructed for a non-simulated provider.

Starting the demo records the previous per-zone control-enabled state and
enables only its authorized simulated zones after fresh critical state passes
the data-quality gate. Stop, reset, natural completion, or a failed start
restores the normal services and disables controls that the demo enabled. A
latched provider fail-safe is never cleared or re-enabled by cleanup. Existing
operator audit records and control-state events are retained.

The demo always uses the existing deterministic `MockIntelligenceProvider`
for its bounded run; the application's configured provider is not changed.
The status API and Demo panel identify the active demo profile.

## Deterministic observations and attribution

The focused scenario fixture uses the configured `classroom_01` capacity and
comfort range with 17 simulated occupants, 26°C, and a 20°C starting setpoint.
The existing mock advisory deterministically selects the comfort midpoint
(24°C) for this medium-occupancy state; the demo profile permits at most the
narrowest configured demo-zone comfort-band width, so this 4°C change remains
inside both the command policy and zone comfort range. An unchanged 17-person
snapshot holds the interval; the fixture then observes 11 people and completes
the interval. The application Demo Mode continues to use its established four
occupancy phases rather than replacing them with this focused test fixture.

The fixture and application use the existing simulated providers. The checked
in mock tariff is a fixed `0.15 USD/kWh` observation from
`MockTariffProvider`; it is not a newly generated or randomized price. Each
`ZoneStateService` snapshot retains the source, observation time, quality, and
simulated provenance, and the existing `TelemetryPersistenceService` stores
eligible energy and tariff boundaries. Interval attribution requires exact
persisted cumulative-energy boundaries with compatible timestamp, unit,
quality, source, and simulated provenance. Energy is the nonnegative kWh delta;
power is never integrated. Cost uses the same validated flat rate only when
tariff coverage remains compatible.

The existing `HVACSimulator` responds deterministically to occupancy,
temperature, setpoint, and elapsed simulated time. This model is illustrative,
not physical building behavior. If a persisted boundary or tariff coverage is
not valid, the interval reports impact as unavailable. No baseline comparison
or energy savings calculation is introduced. UI labels continue to say
`SIMULATED` / `NOT METER DATA`, and savings remain “Not yet measurable” without
a validated comparison baseline.

Each phase's HVAC/energy step is now applied when its observation is emitted,
before the next occupancy transition can close an interval. Phase playback
speed changes the modeled elapsed duration per phase (`phase duration / speed`);
it does not stamp an observation into the future. Observation timestamps come
from the existing UTC clock when occupancy, HVAC, tariff, or control
observations are actually produced. Natural demo completion emits a final
simulated HVAC/energy sample before closing any remaining active interval.
Stop, reset, restart, and normal completion close active intervals against a
persisted current `ZoneState` before the simulated cumulative-energy counter
can be reset. If that final sample fails existing quality, timestamp,
provenance, or monotonicity checks, attribution remains unavailable/invalid.

## Lifecycle and recovery

An unchanged occupancy observation holds the active interval and does not
issue a repeated command or reset the interval start. A later valid changed
occupancy closes the interval, attempts attribution from persisted boundaries,
emits the existing completion/impact/baseline-unavailable events, and then
continues through the ordinary recommendation and safety path. Any subsequent
command is subject to the same demo-only gates; it is not created by interval
completion itself.

`OptimizationIntervalService` and its SQLAlchemy repository restore active and
completed records from the database. Reconstructing these services performs
no control writes. Runtime sensor state and event history retain their
existing process lifecycle. The focused end-to-end test exercises an active
interval, hold, occupancy change, persisted energy/cost attribution, history
recovery, and verifies that service reconstruction does not issue a command.

## Verification limits

This is software-loop validation using simulated occupancy, mock intelligence,
simulated HVAC/control, a fixed mock tariff, and simulated cumulative energy.
It does not establish real BACnet operation, physical HVAC control, meter
accuracy, building energy savings, or production readiness.

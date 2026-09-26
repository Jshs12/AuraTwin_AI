# AuraTwin AI Demo Mode

The Demo Mode is a deterministic software simulation used to demonstrate AuraTwin AI's control architecture. It is not connected to a physical building.

## Purpose and controls

The Overview page includes **AURATWIN DEMO MODE**. START begins a fresh run and starts the existing monitoring scheduler in demo-input mode. PAUSE/RESUME affect the scenario clock, STOP stops the scenario and monitoring loop, RESET clears the scenario's event trace and restores simulated zone/control state, and the speed selector supports 0.5x, 1x, 2x, and 5x. Speed changes only the scenario clock; safety limits are unchanged. Phase duration defaults to 20 simulated seconds and can be configured with `DEMO_PHASE_DURATION_SECONDS`.

## Scenario inputs

The four monitored zones receive deterministic occupancy inputs in this order:

| Phase | classroom_01 | classroom_02 | lab_01 | lab_02 |
|---|---:|---:|---:|---:|
| Low occupancy | 2 | 0 | 8 | 4 |
| Occupancy rise | 18 | 25 | 15 | 14 |
| High occupancy | 35 | 40 | 20 | 20 |
| Occupancy fall | 8 | 10 | 10 | 12 |

These are simulation inputs. Demo mode does not write to YOLO or claim that simulated counts are camera measurements. Manual image inference continues to use the configured occupancy provider.

## Architecture and event lifecycle

`DemoScenarioEngine` owns the scenario state machine and clock. `DemoScenarioOccupancyProvider` supplies only explicit simulated occupancy events. The existing monitoring scheduler consumes those inputs and calls `ZoneStateService`, the configured `RecommendationWorkflow`, mandatory `SafetyConstraintService` validation, and `ControlService`. Only the existing simulated BACnet-ready provider receives a validated control decision. It produces simulated acknowledgement, HVAC response, and energy events. The event context tags each emitted event with the `scenario_id` and `simulation: true` for correlation. Existing WebSocket event delivery and bounded event history are reused.

The mock intelligence provider remains the configured provider. Fallback recommendations continue to pass safety validation. Scenario speed does not affect comfort bounds, confidence threshold, or setpoint-change limits.

## Simulated metrics

The energy chart and its Current/Average/Peak values use the same rolling backend building telemetry samples (up to 120). Each sample is derived deterministically from a 2.0 kW base load, the current simulated per-zone HVAC load, and a 0.015 kW-per-person occupancy load. The simulated HVAC load responds to temperature error from setpoint, occupancy, operating mode, and setpoint changes. Accumulated energy advances with scenario time. Samples are delivered through the existing event WebSocket and are also returned by the building-summary API. Reset clears the rolling samples and accumulated demo energy.

The model is: "Deterministic software simulation used to demonstrate the relationship between occupancy, HVAC operation, and energy telemetry." It is not a meter reading, measured building load, validated energy model, or estimate of savings.

The zone detail API returns the cached scenario occupancy and current simulated HVAC state while a demo scenario is active. Outside the scenario it uses the configured occupancy provider. Event records remain historical. The Overview control list shows only each zone's latest command and explicitly labels occupancy as the value at command time.

## Verified and unverified

Verified by the local demo flow: deterministic phase inputs; recommendation workflow; safety validation; simulated control acknowledgement; illustrative HVAC response; accumulated simulated energy; event trace and WebSocket delivery.

Unverified: physical cameras, CCTV, BACnet/IP, controllers, HVAC equipment, energy meters, building telemetry, actual energy consumption, energy savings, and production deployment. Lyzr live verification remains unresolved and is not part of Demo Mode.

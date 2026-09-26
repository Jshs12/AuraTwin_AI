# BACnet-ready building control architecture

## Current boundary

`ControlService` is the authoritative control boundary. It revalidates every submitted recommendation against fresh zone state before invoking `BuildingControlProvider`. The selected provider is `SimulatedBACnetBuildingControlProvider`, configured by `BUILDING_CONTROL_PROVIDER=simulated_bacnet` (the application default). It uses no BACnet library, socket, device address, or physical controller.

The simulator has per-zone setpoint, request, temperature, HVAC mode, fan, power, energy, and command timestamp state. A command produces a structured `ControlResult`. Deterministic failure modes can exercise provider unavailability, failed acknowledgement, or failed HVAC response; they are test controls and are not enabled in the normal demo.

## Semantic point mapping

The simulator exposes provider-neutral semantic points. It deliberately does not assign BACnet object types or instance numbers.

| AuraTwin semantic field | Simulator point | Future integration mapping |
| --- | --- | --- |
| `ZoneState.temperature` | `temperature_present_value` (read-only) | Configured temperature sensor present-value point |
| `ZoneState.occupancy.people_count` | `occupancy_present_value` (read-only) | Configured occupancy sensor point |
| validated requested setpoint | `cooling_setpoint` (commandable) | Configured commandable analog point, if supported |
| configured heating target | `heating_setpoint` (read-only placeholder) | Configured heating setpoint point if later supported |
| simulator response | `hvac_mode`, `fan_status` (read-only) | Configured equipment status points |

Exact BACnet object identifiers and writable point mappings must come from a site-specific controller configuration; none are invented here.

## Command lifecycle

```text
RecommendationWorkflow
        ↓
SafetyConstraintService
        ↓
ControlService (fresh-state revalidation)
        ↓
BuildingControlProvider
        ↓
SimulatedBACnetBuildingControlProvider
        ↓
HVACSimulator → HVAC_RESPONSE → ENERGY_UPDATE
```

Successful commands emit `CONTROL_COMMAND_REQUESTED`, `CONTROL_VALIDATION`, `CONTROL_COMMAND_SENT`, `CONTROL_ACKNOWLEDGED`, `HVAC_RESPONSE`, and `ENERGY_UPDATE`, sharing a command ID and recommendation reference where available. Rejection and simulated failure events carry failed status and safe error codes. Event history remains bounded by the existing `EventTrace` limit.

## Future physical integration

```text
AuraTwin
   ↓
ControlService
   ↓
BACnetBuildingControlProvider
   ↓
BACnet/IP
   ↓
Building Controller
   ↓
HVAC
```

A future provider must implement the same provider boundary and return structured acknowledgements. It must not bypass `SafetyConstraintService` or let an intelligence provider issue commands directly. Device addressing, object mapping, authentication, network behavior, acknowledgement semantics, and hardware failure policy require site-specific design and commissioning.

## Verification status

### Currently verified in this codebase

- Simulated building-control provider boundary and structured command result.
- Simulated HVAC response, temperature movement, operating mode, power, and accumulated simulated energy.
- Recommendation workflow and safety validation remain upstream of control.
- Provider-neutral point representation and explicit simulated provenance.

### Not verified

- Real BACnet/IP communication.
- A physical BACnet controller or real HVAC actuator.
- Real building telemetry.
- Actual energy savings or production readiness.

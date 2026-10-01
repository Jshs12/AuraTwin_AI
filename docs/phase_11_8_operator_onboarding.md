# Phase 11.8 — Operator Onboarding and UI Acceptance

## Onboarding hierarchy

The authenticated OPERATOR workflow is Organization → Building → Floor → Zone → Integration → Device → Point → Mapping → Observation. Organizations are provisioned by the account administrator; this repository exposes authorized organization read APIs but no organization creation API. Operators can create a building under an organization to which they belong, then add floors and zones. Building, floor, and zone creation use the existing persistent configuration repository and server-side building checks.

The Integrations view exposes the subsequent configuration workflow. Integrations, devices, points, and mappings remain scoped to their building and use existing APIs. A simulated observation is explicitly operator-entered and cannot perform network or device I/O. The readiness panel describes the currently selected integration/device/point context and does not imply production readiness.

## Integration lifecycle and runtime readiness

Connection test means simulated configuration validation only. Hardware connection and discovery are NOT IMPLEMENTED. Devices and points are manually configured. Mapping statuses remain UNMAPPED, SUGGESTED, CONFIRMED, REJECTED, or INACTIVE; confidence is displayed only when supplied. Occupancy, temperature, and cooling_setpoint can update runtime state only after a confirmed mapping and the existing validation/freshness checks. Power, energy, and tariff remain historical-only. Historical telemetry is never promoted automatically to current ZoneState.

## Demo and configured building scope

The demo scenario's selected zone set remains a demo-only selection. UI labels show the demo selection separately from the configured building's active zone count. Starting configured-building monitoring uses the authorized configured building scope. Simulated occupancy, energy, and HVAC values remain labeled as simulated; energy charts say NOT METER DATA and do not claim savings.

## Control safety and roles

The backend continues to enforce command limits, data quality, freshness, recommendation validation, manual override, fail-safe state, and provider readiness. `/api/control/policy-status` is an authenticated OPERATOR-only read endpoint and returns readiness plus missing/invalid variable names, never configured values. The UI disables re-enable when this status is not ready; the backend remains authoritative. ADMIN is oversight-only. Roles remain exactly ADMIN and OPERATOR, with authorization enforced server-side.

## Browser acceptance

Sign in as an assigned OPERATOR and inspect Overview, Zones, Occupancy, Energy, Events, Integrations, and Access. Verify simulated labels and separate runtime/history displays. Verify socket state and inspect Console/Network manually. A command-policy 409 is an expected safety response and should be rendered as CONTROL UNAVAILABLE with backend-provided field names; unrelated request failures remain visible. No hardware connection, real sensor accuracy, real energy savings, or production readiness is claimed.

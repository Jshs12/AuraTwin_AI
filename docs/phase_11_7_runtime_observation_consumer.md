# Phase 11.7 — Runtime Observation Consumer and Dashboard Reliability

## Runtime consumer boundary

The existing flow now wires `ProviderObservationIngestionService` to
`ZoneStateRuntimeConsumer`, which delegates into the existing
`ZoneStateService`. An observation must resolve through an active configured
integration, device, readable point and `CONFIRMED` mapping to its exact zone,
floor, building and organization. The service validates the value, unit,
observation time, upstream quality and the configured Phase 10 freshness
policy before persistence. The consumer rechecks the composite critical
ZoneState quality before storing the current overlay. Failed runtime validation
does not undo an otherwise accepted historical telemetry row, and it does not
alter ZoneState.

The new authenticated `POST /api/point-mappings/{point_id}/simulated-observation`
is a manual demo input only. It accepts a numeric value and an explicit
timezone-aware observation timestamp. Mapping identity and simulated
provenance come from the configured records and the explicit-value simulated
adapter; the client cannot claim hardware provenance. It performs no network
or device reads. Only authorized OPERATOR building scope can use this
configuration action. ADMIN remains oversight-only.

## Historical telemetry vs runtime state

Historical observations are persisted through the existing telemetry
repository. They do not become live state merely because they are newest in
history. Runtime influence requires the explicit `runtime_input` flag from the
current observation adapter and the configured consumer. Current observations
are held in the existing `ZoneStateService` overlay and are rechecked with the
existing data-quality report on each state read. No second runtime store or
telemetry system was added.

Runtime-eligible logical signals:

- `occupancy`: count, bounded by configured zone capacity.
- `temperature`: current temperature.
- `cooling_setpoint`: current observed setpoint; this does not issue a command.

Historical-only signals:

- `power`: instantaneous power telemetry only.
- `energy`: cumulative energy telemetry only; it is not converted to power.
- `tariff_rate` and `cost`: historical/advisory telemetry only.

The existing configured data-quality policy remains the only freshness
authority. Missing thresholds are not replaced with new defaults. Each
runtime update is rejected if occupancy, temperature or current setpoint
fails the existing critical-signal policy.

## WebSocket lifecycle

The authenticated `/api/monitoring/ws/events` endpoint still checks the JWT,
role permission and assigned building zones before accepting a connection.
It rechecks current identity/scope on outgoing events and at a 15-second idle
interval, closes on token expiry/revocation or lost access, and unsubscribes
on disconnect. Browser auth remains required; no public event stream was
introduced.

`main.tsx` enables React `StrictMode`, which replays effects in development.
The previous monitoring hook created a socket immediately and cleanup closed
it while still `CONNECTING`; that canceled handshake explains the browser's
“closed before the connection is established” warning even though a later
socket could deliver events. The hook now defers socket creation until after
the replay cleanup window, closes active sockets on unmount/logout, lets
in-flight handshakes close on open, stops reconnecting on 4401/4403 auth or
authorization closes, and caps transient retries at five with backoff. The
Events view and monitoring panel show the connection state and allow a manual
retry after transient retries are exhausted.

## Control 409 safety UX

An incomplete or invalid `COMMAND_LIMIT_*` policy remains a 409 fail-closed
rejection. The response now includes a stable code and only the names of
missing/invalid configuration variables; it never returns configured numeric
values. Data-quality rejection reports signal state/reason. The zone panel
renders an explicit `CONTROL UNAVAILABLE` state for command policy failures.
No safety condition was relaxed.

## Demo scope and configured monitoring

The four selected demo IDs are owned by `DemoScenarioEngine.ZONES` and its
four deterministic scenario phases. `POST /api/demo/start` intentionally
configures only this demo-specific subset. Regular autonomous monitoring
continues to derive its scope from every active authorized zone returned by
the persistent building configuration. Monitoring status now identifies
`DEMO_SCENARIO`, `CONFIGURED_BUILDING`, or `STOPPED` and reports both the
configured zone count and demo-scenario count. The UI explains a 4-of-10 view
as the selected demo subset, not a building limit. `ZoneMonitoringScheduler`
accepts arbitrary configured zone counts.

## Integration lifecycle and provenance

The integrations view keeps `CONFIGURATION ONLY` and explicitly labels
configuration testing as simulated, discovery as unavailable, and manual
device/point mapping as configuration work. Confirmed points may accept an
explicit simulated observation. UI values retain source, quality and
`SIMULATED` provenance; historical data is separate from current ZoneState.

The existing camera flow stores only derived occupancy counts; this phase adds
no image/video fields or storage. Power, energy and tariff values remain
simulated or provider-reported scalar observations and do not establish meter
connectivity, actual savings, or real-building operation. Supabase, real
BACnet/IP, RTSP and energy-meter providers remain unconnected/unimplemented.

## Verification notes

Automated tests exercise authorization, mapping, quality/freshness, current
ZoneState overlays, historical/runtime separation, scope mismatch, websocket
subscription cleanup and arbitrary configured monitoring scopes. Localhost
API authorization and frontend build are separately verified. Browser
authentication/devtools inspection must be reported separately if the
available computer-use surface cannot perform a user login; successful API
tests are not browser-console evidence.

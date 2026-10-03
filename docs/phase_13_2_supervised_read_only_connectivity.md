# Phase 13.2 — Supervised Read-Only Adapter Connectivity

## Scope and current availability

Phase 13.2 adds an explicit adapter registry and operator-triggered connectivity boundary while retaining the Phase 13.1 integration, device, point-mapping, lifecycle, telemetry, and runtime-consumer records. The default registry contains explicit unavailable adapters for BACnet/IP, RTSP camera, and energy meter protocols. No protocol driver is installed or registered in this repository, so a connection attempt returns `ERROR / ADAPTER_UNAVAILABLE`; it never claims a physical device exists.

The deterministic simulated discovery fixtures remain available through the existing discovery action. Their `SIMULATED_FIXTURE` provenance and no-network message are preserved. A fixture result is not physical discovery.

## Adapter boundary and lifecycle

`AdapterRegistry` creates adapters from the configured integration type and non-secret configuration only. A future driver must implement the existing `IntegrationAdapter` contract, report health and capabilities, enforce its own native I/O timeout, and keep `can_write` false. Phase 13.2 rejects a connection success if the adapter advertises write capability. There is no ControlService dependency or command method in this layer.

The OPERATOR explicitly invokes `POST /api/integrations/{integration_id}/connect`. One request transitions through the persisted Phase 13.1 lifecycle (`DISCONNECTED → CONNECTING → CONNECTED|ERROR`). A later operator request is the only retry mechanism. `POST .../disconnect` invokes bounded close and persists the resulting state. At startup, persisted active states are reconciled to `DISCONNECTED` because adapter instances are process-local; at shutdown active adapters are closed. No retry task or polling loop is created.

The existing `POST .../test-connection` remains local configuration validation and does not establish a connection. Configuration validation and adapter connection are distinct operations.

The outer operation wait is configurable with `INTEGRATION_ADAPTER_TIMEOUT_SECONDS` and defaults to 5 seconds. Synchronous Python threads cannot be forcibly stopped after an outer timeout; any future physical driver must also configure protocol/socket-level deadlines and cancellation. No physical driver currently runs.

## Read-only discovery and observations

The existing integration discovery action continues loading deterministic fixtures while disconnected. If a connected registered adapter advertises read-only device discovery, its `DiscoveryResult` can be consumed through that same action. Device point discovery is an explicit `POST /api/devices/{device_id}/discover-points`; the route whitelists safe point metadata, persists suggestions only, forces discovered point mappings to `writable=false`, and never confirms a mapping.

An explicit `POST /api/integrations/{integration_id}/poll` requests one observation for each confirmed, readable point from a connected adapter. The adapter must return the existing strict `ProviderObservation` schema. The route checks observation identities and passes records to `ProviderObservationIngestionService`; that service rechecks ownership, active configuration, confirmed mapping, signal, unit, quality, freshness, persistence, and optional runtime consumer rules. Polling never calls control. Unknown response shapes and adapter exceptions produce sanitized reason codes.

The existing telemetry signal mapping supports occupancy, temperature, instantaneous power, cumulative energy, tariff rate/cost and cooling setpoint. They are not interchangeable: cumulative kWh remains cumulative energy, not instantaneous kW. The runtime consumer retains its existing eligible runtime signals (occupancy, temperature and cooling setpoint); energy and tariff observations remain historical/analytics inputs. Camera frame acquisition and occupancy inference are not connected to this registry, and no image or video payload is stored.

No new telemetry store or runtime state store is introduced. Historical telemetry becomes current ZoneState only under the existing explicit `runtime_input` policy; reads from history do not update current state.

## Provenance and security

Connection responses, health and lifecycle endpoints do not return credentials, configuration connection strings, raw exception messages, or authentication material. Health errors are allow-listed or reduced to `PROVIDER_FAILURE`. Observation source, simulated flag, timestamp, quality, unit and signal are retained through the existing point mapping and ingestion result.

The existing role and building checks remain in force: ADMIN can observe; OPERATOR performs integration actions only for assigned buildings. Mappings stay unconfirmed until an OPERATOR confirms them. Real provider data is marked non-simulated only when a registered adapter returns that provenance after connection; unavailable adapters and fixtures are explicitly simulated/not connected.

## Control boundary and limitations

This adapter layer performs only connection tests, discovery, reads, health, and close. It does not call `ControlService`, send BACnet writes, set HVAC points, enable autonomous control, access camera video storage, or write meter values. Existing recommendation, data-quality, freshness, safety, command-limit, manual-override, fail-safe and ControlService gates remain the only route to simulated HVAC commands.

Phase 13.2 does NOT establish real building connectivity unless a physical adapter is actually configured and successfully connected. In this repository all physical protocol adapters are unavailable. There are no real BACnet reads, RTSP frames, meter observations, real energy savings, or hardware-control guarantees. The application’s simulated providers remain separate and available for development/demo use.

No migration was required; Phase 13.1 integration/device/point/lifecycle records are reused. Alembic head remains `20261003_09`.

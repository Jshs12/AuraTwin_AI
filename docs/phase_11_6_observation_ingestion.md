# Phase 11.6 — Provider-to-point observation ingestion

## Authorization regression and bootstrap repair

The localhost OPERATOR had a persistent `organization_memberships` row but no `user_building_access` assignment. `AuthService` only attempted to add the bootstrap operator when the account did not already exist. Because the configured account already existed, startup never reconciled its configured building assignment. Consequently its authenticated profile had no building IDs; `/api/zones` and monitoring scope returned `Building access denied`, while demo endpoints rejected the demo zones as outside the operator's assigned scope. Authentication and role permissions were functioning; persisted building access was missing.

The SQLAlchemy user repository now idempotently ensures the configured bootstrap OPERATOR has the configured `AURATWIN_BOOTSTRAP_OPERATOR_BUILDING_ID` assignment and matching organization membership. It resolves the building server-side by UUID or stable building key, changes no role, preserves other assignments, and fails closed if the configured building does not exist. This remains in the existing database authorization model; no endpoint/frontend bypass was introduced.

## Configuration, observation, runtime, history, and control

These remain distinct concepts:

- **Configuration:** Integration, device, point, logical signal, and explicit mapped zone are persistent commissioning metadata.
- **Observation:** A provider-reported scalar with a required observation timestamp, source, quality, and simulated flag.
- **Runtime state:** Remains owned by the existing ZoneState/provider path. Ingestion does not automatically replace a live control input.
- **Historical telemetry:** Accepted observations use the Phase 11.3 telemetry service/repository/table, with server-resolved tenant scope and existing idempotency/retention.
- **Control command:** This service has no control-provider dependency and cannot issue HVAC commands.

## Provider contract and mapping resolution

`ProviderObservation` identifies integration, device, and point mapping and requires `observed_at`, finite numeric value, source, and `simulated`. The schema rejects missing/naive timestamps and extra fields (including image/video payloads). Optional upstream quality and an explicit `runtime_input` designation are carried separately.

Each logical point mapping now has an explicit nullable `zone_id`; it is never inferred from device name. Ingestion joins the point through device, integration, zone, floor, building, and organization and verifies the chain belongs together. Integration/device must be configured and active; the point must be readable, `CONFIRMED`, have a valid telemetry signal, compatible scalar data type/unit, and an explicit active zone. Only `CONFIRMED` is ingestible. `UNMAPPED`, `SUGGESTED`, `REJECTED`, and `INACTIVE` are rejected.

The mapper supports existing telemetry signals only: occupancy, temperature, power, energy, cost, and tariff rate. Control state signals such as setpoint or fan mode are not silently coerced into telemetry. Occupancy must be a whole-number count within configured zone capacity. Nonnegative signals are checked for negative values. Other range/freshness rules use `DataQualityGate` configuration; no production thresholds are added. Upstream non-VALID quality is preserved and rejected. Stale/future/missing/invalid data fails closed.

## Persistence and current runtime

Accepted values become the existing `TelemetryObservation` and use `TelemetryPersistenceService.persist_observation`, which verifies all scope IDs against the persistent zone lineage before calling the existing repository. Existing idempotency and retention remain authoritative; no telemetry table/repository was added. The API offers a building-authorized latest-observation view for configured points.

Ordinary ingested values remain historical. A `RuntimeObservationConsumer` protocol exists for a future provider that explicitly designates current runtime inputs. No consumer is connected in this phase; a requested runtime update is rejected if that consumer is absent. The existing ZoneState builder and quality/control safety gates remain unchanged.

## Simulated provider and privacy

`ExplicitValueSimulatedProvider` wraps only caller-supplied test/demo values, reads no device, emits only confirmed readable points, and always marks output simulated. No simulated observations are injected automatically at startup. Camera/image/video payloads are not part of the observation contract and are never persisted; only scalar derived occupancy is representable.

The Phase 11.5 configuration test remains local and simulated (`connection_established: false`). Supabase is not connected. Real BACnet/IP, RTSP/camera ingestion, energy-meter protocols, credentials resolution, and device discovery are not implemented.

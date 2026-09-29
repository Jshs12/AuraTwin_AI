# AuraTwin AI — Phase 9 System Status and Integration Readiness

## Product boundary

AuraTwin AI is currently a local software prototype for occupancy-informed HVAC advisory workflows. Its configured demo uses YOLO for local image inference, mock intelligence, simulated building control/HVAC, deterministic simulated energy telemetry, and in-memory events. No external AI, BACnet device, production camera, or energy meter was contacted for this audit.

## Architecture and data flow

```text
Camera/image or explicit Demo Mode occupancy ─┐
Temperature, energy, tariff, control state ────┴→ ZoneState
  → RecommendationWorkflow
  → IntelligenceProvider or deterministic optimizer fallback
  → SafetyConstraintService
  → ControlService (fresh state + recommendation age + provider readiness)
  → BuildingControlProvider
  → simulated BACnet control + HVAC response
  → energy telemetry events
  → REST/WebSocket dashboard
```

Intelligence providers return advisory recommendations and have no control-service dependency. The shared workflow validates recommendations before control. `ControlService` revalidates against the current state immediately before a provider write, checks recommendation age, and now rejects an unavailable control provider before calling its write method. The current write target is simulated.

## Components and provider boundaries

| Capability | Boundary/current implementation | Status and failure behavior |
|---|---|---|
| Occupancy | `OccupancyProvider`; mock and `YOLOOccupancyProvider` | YOLO loads a project-relative model, filters the person class, reports ready state, and rejects invalid image bytes. Inference operation is verified; accuracy against ground truth is not. |
| Camera | `CameraProvider`; mock sample images and `RTSPCameraProvider` | Mock is used by current monitoring configuration. RTSP URL comes from per-zone environment variables; FFmpeg open/read have bounded configurable timeouts. A call returns no frame on failure; the next monitoring cycle retries by opening a new capture. Camera I/O runs in a worker thread. No real camera has been connected or tested. |
| Temperature | `TemperatureProvider`; fixed mock source and simulated HVAC feedback | Current demo uses mock/simulated readings. No physical sensor provider is implemented. |
| Energy | `EnergyProvider`; mock readings, plus deterministic building telemetry and illustrative per-zone mock stream | All current readings are simulated. No meter integration is implemented. The provider boundary needs a richer future meter contract for meter ID, timestamp, cumulative energy, quality, and status. |
| Tariff | `TariffProvider`; flat mock tariff | Rate is represented as currency/kWh. Time-of-use and site-verified tariffs are not implemented/configured. |
| Intelligence | `IntelligenceProvider`; deterministic mock and Lyzr v3 adapter | Mock is the current demo. Lyzr transport, schema parsing, timeout and sanitized failure handling are implemented, but live verification is unresolved. |
| Building control | `BuildingControlProvider`; `SimulatedBACnetBuildingControlProvider` | Semantic points, readiness, structured acknowledgement, failure modes, and simulated HVAC response are implemented. It does not speak BACnet/IP. |
| HVAC | `HVACSimulator` behind simulated control behavior | Deterministic illustrative model, not engineering-grade thermal physics or connected HVAC equipment. |
| Optimization | `OptimizationEngine` | Deterministic rule-based optimizer, not AI. It uses occupancy/comfort state and a simple cost heuristic; building-level constrained optimization is a future extension. |

## APIs and state

FastAPI exposes health, zone/state/history, recommendation/control, occupancy inference/status, monitoring, Demo Mode, building summary/activity/events, control status/points/results, and placeholder telemetry/BACnet/Lyzr/n8n status routes. Runtime events and last control results are held in process memory. There is no production database, durable event store, historical analytics repository, or multi-process state coordination.

Monitoring and Demo Mode use separate input paths: normal monitoring obtains frames through its configured camera and occupancy providers; Demo Mode feeds deterministic scenario counts to the same state/recommendation/safety/control path. Demo completion and reset stop background tasks. Reset clears Demo Mode event context, telemetry, scenario state, and demo control activity; it is not a global deletion of unrelated event history.

The event store is bounded to 500 events. Events use unique IDs and UTC timestamps and are broadcast over WebSocket. Reconnect behavior has been exercised locally. It is not a durable event bus.

## Safety and intelligence model

`SafetyConstraintService` enforces zone match, allowed action, numeric finite setpoint/confidence, confidence threshold, global and zone comfort bounds, and maximum per-decision setpoint change. Fallback recommendations are validated too. `ControlService` rejects stale recommendations, revalidates against fresh zone state, and rejects unavailable providers before writes. Simulated provider acknowledgements and failure results are structured.

Malformed recommendation diagnostics no longer include Pydantic's raw input echo. Rejected recommendations are not copied into shared event payloads. Lyzr remains optional; no secret is sent to the browser or written to events by the provider implementation. Live Lyzr service acceptance is **UNVERIFIED**.

The safety checks do not constitute commissioned controls engineering, site-specific interlocks, cybersecurity review, or proof of safety for real equipment. Per-signal freshness limits for occupancy, temperature, and setpoint are configurable as documented in `docs/stale_data_protection.md`; no sensor age limits are guessed or enabled by default. Current demo readings carry mock/simulated provenance and do not prove real sensor freshness.

## BACnet and real building control

The simulator is behind the provider interface and does not claim BACnet device connectivity. A future site-specific provider/configuration would need to define site, device, zone, object/point, property, units, read/write permissions, bounds, network timeout, authentication, write acknowledgement, and commissioning procedure. No device address or BACnet object identifier is supplied here; no real provider is implemented. “BACnet-ready architecture” means a software boundary exists, not that a BACnet device is connected.

## Energy units and savings

- Power: kW.
- Energy: kWh, integrated as power × elapsed hours.
- Tariff: currency/kWh.
- Cost: currency, calculated from kWh × configured rate.
- The known-value test verifies 10 kW × 0.5 hours = 5 kWh.
- Demo playback speed changes wall-clock playback only and does not multiply simulated elapsed energy.
- Building demo energy is aggregated from the four configured simulated zones and recorded in telemetry events; the dashboard plots these samples. The non-Demo mock stream is illustrative, per-zone, and separately labeled; it is not building-level aggregation.
- Rolling current/average/peak power metrics are based on the retained sample window; they are not a site meter demand interval.

No savings claim is supported. Defensible savings analysis would require a real meter, a measured baseline and optimized period, and normalization for occupancy, weather, operating hours, tariff, and building conditions. Comfort duration/violation tracking and verified occupant outcomes are not implemented.

## Computer vision and RTSP

Manual uploaded-image inference is separate from Demo Mode's deterministic occupancy inputs. YOLO counts person-class detections; it does not identify people or infer demographics. Existing local inference tests establish that the model runs, not production accuracy, real-time site performance, or camera coverage.

RTSP is a configuration-driven adapter only. It uses bounded FFmpeg open/read timeouts and avoids printing configured URLs. Each monitoring cycle creates a fresh capture, so recovery is a later-cycle retry rather than a managed persistent reconnect state machine. No production RTSP stream has been verified.
Camera capture runs in a worker thread so a slow or timing-out RTSP capture does not block the FastAPI event loop.

## Frontend data labels

The dashboard distinguishes Demo, YOLO, mock, and unknown occupancy sources; mock intelligence; simulated control; and deterministic/illustrative energy. The energy graph consumes telemetry samples/events rather than arbitrary animation. Annotated image responses expose an asset name instead of a local filesystem path, and the browser builds its static URL from the current host. No dashboard metric represents real building energy or savings.

## Configuration and security

Configuration loads from the project-root `.env` with process environment values taking precedence. Relative model/data paths resolve from the project root. Monitoring/scenario timing values are bounded and invalid camera selection falls back to the mock provider. `.env` is ignored; `.env.example` contains no real secret values, only placeholders and non-secret defaults. Generated YOLO detections and demo log files are now ignored by Git. The RTSP adapter does not log URLs. YOLO model status reports a basename or project-relative path, not an arbitrary absolute external path. Occupancy inference API failures return generic client diagnostics; detailed provider failures remain server-side.

The local API has no authentication and uses permissive development CORS; do not expose it to an untrusted network. Event history is in memory. These are development/demo constraints, not production controls.

## Verification performed

- Backend suite after Phase 9 hardening: 118 passed, 0 failed, 0 skipped; two environment warnings were observed (Starlette `TestClient`/`httpx` deprecation and pytest cache write permission).
- Frontend production build passed after Phase 9 hardening.
- Clean local restart returned backend and frontend HTTP 200; YOLO reported ready, intelligence returned mock, control reported simulated BACnet.
- Monitoring start/stop was exercised. A recommendation passed validation, simulated control acknowledged it, and expected control/HVAC/energy events appeared.
- Complete Demo Mode lifecycle and reset were exercised. The run emitted 276 unique chronological events; monitoring stopped on completion and reset returned the app to IDLE.
- Lifecycle duplicate transitions returned expected conflicts and reset/start returned the application to a clean scenario.
- Three WebSocket connect/disconnect/reconnect sessions delivered unique event IDs.
- Regression tests cover RTSP timeout parameterization and URL redaction, malformed/untrusted recommendation non-echo, inference error sanitization, and provider-readiness rejection.

## Remaining readiness gaps

- **UNVERIFIED:** live Lyzr, production camera/RTSP, model accuracy, physical BACnet, HVAC equipment, and real energy meter.
- **NOT IMPLEMENTED:** durable telemetry/event/analytics storage, measured savings methodology, production authentication/authorization, site commissioning, comprehensive comfort-violation duration analytics, and a real building-control provider.
- **FUTURE:** site-specific BACnet mapping and commissioning, meter ingestion with quality metadata, time-of-use tariff source, persistence, and building-level optimization after real operational constraints are defined.

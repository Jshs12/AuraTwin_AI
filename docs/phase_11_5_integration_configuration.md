# Phase 11.5 — Integration configuration and mapping foundation

## Models and lifecycle

Phase 11.5 extends the Phase 11.1 `integrations`, `devices`, and `point_mappings` tables rather than creating parallel entities. Integrations are building-owned and typed as `BACNET`, `CAMERA`, or `ENERGY_METER`; devices belong to integrations and can optionally reference a zone in the same building. The existing point-mapping record represents an external point plus its AuraTwin logical signal. Its `mapping_status` lifecycle is `UNMAPPED`, `SUGGESTED`, `CONFIRMED`, `REJECTED`, or `INACTIVE`. Confidence is optional and must be explicitly supplied for a suggestion; the API never invents it. Operator actions mark a mapping as operator-sourced.

Integrations and devices use `CONFIGURED` or `DISABLED` operational status; integration archival and device archival preserve configuration history. The point table's unique device/external point identity constraint remains in force. A logical signal mapping is configuration metadata only and does not create telemetry observations.

## APIs and authorization

Authenticated reads require building-read permission. Mutations require `INTEGRATIONS_CONFIGURE`, which is granted to OPERATOR under the existing role policy; ADMIN remains oversight-only. Every nested operation resolves the integration's building server-side and applies existing building access checks. A device zone must resolve to the same building. Client-provided organization IDs are not used for authorization.

The operator UI is the dashboard's **Integrations** section, scoped to its current authorized building. It allows basic integration, device, and point/mapping configuration and mapping confirmation or rejection. The API remains the complete interface for editing and disable/archive actions.

## Connection test and discovery

The connection tester is an injectable interface whose Phase 11.5 implementation validates only that local configuration exists and is enabled. It always returns `simulated: true` and `connection_established: false`; no network/hardware request is made. Discovery is an injectable interface whose current provider reports `performed: false` and returns no candidates. Real BACnet/IP discovery and real camera or meter tests are not implemented.

## Credentials, privacy, and runtime boundaries

Integration configuration and point metadata reject common secret-bearing field names and URLs containing embedded user information. A credential reference may be supplied separately; APIs expose only whether a reference is configured, never the reference itself. This phase adds no secret manager. Camera entries store metadata only and do not store frames, snapshots, or video. The simulated BACnet control provider, Phase 10 safety/control path, and Phase 11.3 telemetry persistence remain separate and unchanged. Mapping a signal never fabricates a runtime observation.

## Database and future work

Migration `20261001_04` adds device manufacturer/model metadata and mapping lifecycle/source/confidence fields. The existing SQLAlchemy/PostgreSQL-compatible database remains in use; SQLite development startup applies Alembic migrations. Supabase is not connected. Real protocol communication, credential resolution, hardware discovery, commissioning validation, and provider-observation ingestion remain later work (including Phase 14 integration work).

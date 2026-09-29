# Phase 9 Security Foundation

## Roles and permissions

AuraTwin has exactly two application roles, with no inheritance hierarchy:

| Role | Permissions |
| --- | --- |
| `ADMIN` | `building:read`, `zones:read`, `telemetry:read`, `energy:read`, `events:read`, `system:read`, `audit:read`, `access:read` |
| `OPERATOR` | `building:read`, `building:configure`, `zones:read`, `zones:create`, `zones:update`, `zones:delete`, `integrations:configure`, `monitoring:manage`, `recommendations:read`, `control:execute`, `access:manage` |

Admins receive organization wide read visibility. Operators receive operational permissions only for assigned buildings. The current repository has one simulated building, `development-building`; zone ownership resolves to that identity until a persistent building repository is introduced. Enforcement is server side.

## Authentication and initial account configuration

The API exposes public `GET /api/health` and `POST /api/auth/login`. Protected `GET /api/auth/me` returns the authenticated profile and assigned building IDs. Login uses Argon2 password hashes and HS256 JWT access tokens. Logout is deliberately honest: the API tells the client to discard its stateless token; it does not claim to revoke it.

Copy the security settings from `.env.example` into the ignored local `.env`. Generate a unique random `AURATWIN_JWT_SECRET` of at least 32 characters. Set `AURATWIN_BOOTSTRAP_ADMIN_EMAIL` and `AURATWIN_BOOTSTRAP_ADMIN_PASSWORD`. Optionally configure `AURATWIN_BOOTSTRAP_OPERATOR_EMAIL` and `AURATWIN_BOOTSTRAP_OPERATOR_PASSWORD`; that operator is assigned to `AURATWIN_BOOTSTRAP_OPERATOR_BUILDING_ID` (default `development-building`). Never commit these values. When a bootstrap account is enabled, the application refuses to start with a missing or unsafe JWT secret.

For account access operations after bootstrap, an operator with `access:manage` can create or deactivate operator accounts through `/api/auth/operators`. Accounts and audit history are currently in memory and reset on process restart; bootstrap accounts are reseeded only when their environment settings are configured. Production provisioning must move to persistent storage.

## API, control, and event stream

All existing API routes except health and login require a validated identity and route permissions. Control requires the operator role, `control:execute`, current building assignment, fresh state, and the existing recommendation/safety validation chain before the simulated control provider can run. Authenticated static YOLO annotation files are also scoped to the current building. The monitoring WebSocket requires an access token and checks current user status and operator building assignment.

CORS reads the comma separated `AURATWIN_CORS_ORIGINS`; local Vite origins are the development default. Wildcard origins are discarded. Browser API calls use a session-storage access token. The role-aware dashboard exposes access information to both roles according to backend permissions, operator account management to operators, and audit history to admins. WebSocket authentication currently uses a query parameter because native browser WebSockets cannot attach an Authorization header; deployments should terminate access logs carefully and migrate to short-lived one-time WebSocket tickets.

## Audit records

The bounded in-memory audit service records successful/failed login, operator creation/revocation, monitoring and demo lifecycle operations, and control attempts. It does not store passwords, hashes, bearer tokens, cookies, or authentication headers. Audit records are readable by `ADMIN` users with `audit:read`.

## Development and production limits

The user repository and audit service are replaceable abstractions, not a Phase 11 database. The current building is one local simulated building. HVAC/BACnet control, occupancy input, and energy values remain mock/simulated as configured; authentication does not make the system a production building controller. Operator zone/building/integration configuration screens and corresponding CRUD APIs are not part of this security foundation. Use TLS, persistent user/access/audit repositories, provisioning and revocation workflows, key rotation, CSRF-aware browser architecture, rate limiting, and security review before any production deployment.

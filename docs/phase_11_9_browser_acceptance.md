# Phase 11.9 — Authenticated Browser Acceptance

## Findings

The screenshot evidence contains HTTP status codes but no request Authorization-header evidence or token-expiry timeline. The screenshots alone cannot establish whether the browser token expired, was revoked, or was invalidated by a backend process/session change. A fresh authenticated OPERATOR session succeeds against the affected monitoring and demo endpoints, and the backend rejects invalid or expired tokens with 401 as intended.

The frontend did contain a concrete request-lifecycle defect that explains the three-request burst: monitoring status, demo events, and demo building summary were started concurrently by one `Promise.all` refresh. Once the mounted dashboard had an invalid session, all three calls could reach the backend with that same token. The shared client cleared the token and dispatched session expiry for each response, while the hook replaced the actual error with a generic backend-unavailable message.

## Hardened session behavior

On startup, the application restores the session by validating `/api/auth/me` and loading `/api/auth/access` before rendering Dashboard. Protected API requests are not sent without a current session token. Requests read the current session token at send time. A 401 clears the matching invalid session once and dispatches a single expiry event with the safe HTTP status and endpoint path; no token, query string, or Authorization header is recorded. A non-authentication failure while restoring the session keeps protected pages paused and presents a retry/sign-out screen.

Monitoring status, demo events, and demo summary are fetched sequentially. The first 401 prevents the rest of that refresh cycle. A 401/403 pauses protected polling; socket authentication/authorization closes do not reconnect. Network reconnects remain bounded. Logout clears sessionStorage and unmounts the protected dashboard, stopping its polling and socket lifecycle.

## Manual acceptance procedure

1. Open a fresh private/incognito browser window and visit `http://127.0.0.1:5173/` (or the configured frontend host).
2. Sign in as an authorized OPERATOR. Do not copy or expose session tokens.
3. Confirm the session bar says `SESSION AUTHENTICATED` and the dashboard appears only after session restoration.
4. Open Overview, Zones, Occupancy, Energy, Events, Integrations, and Access in turn; return to Overview.
5. In DevTools Network, filter to `/api/` and confirm protected requests have an Authorization header without copying its value. Check `/api/monitoring/status`, `/api/demo/building-summary`, and `/api/demo/events` return 200 for the assigned development OPERATOR.
6. In DevTools Console and Network, record any 401, 403, 409, 500, failed request, or runtime error. A command-policy 409 is an expected safety rejection only when the UI presents CONTROL UNAVAILABLE and the backend reason.
7. In DevTools Network → WS, confirm one `/api/monitoring/ws/events` connection opens, remains connected while the session is valid, and does not multiply during page navigation.
8. Sign out. Confirm the app returns to Sign in, the WebSocket closes, and protected polling stops. For expiry testing, wait for a genuine session expiry or use an invalid test session; do not edit tokens in DevTools.

The manual browser/DevTools checklist is not considered complete until a human records the resulting Console, Network, and WS evidence. All HVAC, occupancy, and energy values remain simulated/demo data where labeled; this procedure does not establish hardware connectivity, sensor accuracy, or energy savings.

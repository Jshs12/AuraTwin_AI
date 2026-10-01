import test from "node:test";
import assert from "node:assert/strict";

const values = new Map();
const dispatched = [];
globalThis.sessionStorage = {
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, String(value)),
  removeItem: key => values.delete(key),
};
globalThis.window = {
  location: { protocol: "http:", hostname: "localhost", href: "http://localhost:5173/" },
  dispatchEvent: event => { dispatched.push(event); return true; },
};

const { authFetch, authSession, ApiRequestError, api } = await import("../src/services/api.ts");

test("protected client does not send a request before a session exists", async () => {
  authSession.clear();
  let sent = false;
  globalThis.fetch = async () => { sent = true; return new Response("{}", { status: 200 }); };
  await assert.rejects(authFetch("/api/zones"), error => error instanceof ApiRequestError
    && error.status === 401 && error.authenticationState === "anonymous" && error.endpoint === "/api/zones");
  assert.equal(sent, false);
});

test("each request reads the current session token instead of caching it", async () => {
  const observed = [];
  globalThis.fetch = async (_input, init) => {
    observed.push(new Headers(init.headers).get("Authorization"));
    return new Response("{}", { status: 200 });
  };
  authSession.setToken("session-one"); await authFetch("/api/one");
  authSession.setToken("session-two"); await authFetch("/api/two");
  assert.deepEqual(observed, ["Bearer session-one", "Bearer session-two"]);
});

test("concurrent 401 responses clear one session and emit one expiry event with safe diagnostics", async () => {
  dispatched.length = 0;
  authSession.setToken("expired-session");
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Invalid or expired authentication" }), {
    status: 401, headers: { "Content-Type": "application/json" },
  });
  const results = await Promise.allSettled([
    authFetch("http://localhost:8000/api/monitoring/status?ignored=private"),
    authFetch("http://localhost:8000/api/demo/events"),
  ]);
  assert.equal(values.size, 0);
  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].detail.status, 401);
  assert.equal(dispatched[0].detail.endpoint, "/api/monitoring/status");
  for (const result of results) {
    assert.equal(result.status, "rejected");
    assert.equal(result.reason.status, 401);
    assert.equal(result.reason.authenticationState, "expired");
    assert.equal(result.reason.endpoint.includes("ignored"), false);
  }
});

test("structured safety failures preserve status, endpoint, and safe backend reason", async () => {
  authSession.setToken("valid-session");
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: {
    code: "COMMAND_POLICY_INCOMPLETE", message: "Safety command-limit policy is incomplete.",
    missing_configuration: ["COMMAND_LIMIT_MAX_DELTA"],
  } }), { status: 409, headers: { "Content-Type": "application/json" } });
  await assert.rejects(authFetch("/api/zones/auditorium_01/control-enabled"), error => {
    assert.equal(error.status, 409);
    assert.equal(error.endpoint, "/api/zones/auditorium_01/control-enabled");
    assert.equal(error.code, "COMMAND_POLICY_INCOMPLETE");
    assert.deepEqual(error.missingConfiguration, ["COMMAND_LIMIT_MAX_DELTA"]);
    assert.equal(error.authenticationState, "authenticated");
    return true;
  });
  api.logout();
  assert.equal(authSession.getToken(), null);
});

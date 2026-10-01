import type {
  Zone,
  ZoneState,
  SystemEvent,
  OccupancyDetectionResponse,
  OccupancyStatus,
  MonitoringStatus,
  RecommendationDecision,
  ControlResult,
  DemoStatus,
  DemoBuildingSummary,
  Floor,
  TelemetryAnalyticsResponse,
  TelemetrySignal,
} from "../types/api";

// Keep browser clients attached to the machine serving the UI (localhost,
// loopback IP, or a LAN hostname) instead of the visitor's localhost.
export const API_BASE = `${window.location.protocol}//${window.location.hostname}:8000/api`;
const TOKEN_KEY = "auratwin_access_token";

export class ApiRequestError extends Error {
  code?: string;
  missingConfiguration?: string[];
  invalidConfiguration?: string[];
  constructor(message: string, detail?: Record<string, unknown>) {
    super(message);
    this.name = "ApiRequestError";
    this.code = typeof detail?.code === "string" ? detail.code : undefined;
    this.missingConfiguration = Array.isArray(detail?.missing_configuration)
      ? detail.missing_configuration.filter((item): item is string => typeof item === "string") : undefined;
    this.invalidConfiguration = Array.isArray(detail?.invalid_configuration)
      ? detail.invalid_configuration.filter((item): item is string => typeof item === "string") : undefined;
  }
}

async function throwApiError(response: Response, fallback: string): Promise<never> {
  const body = await response.json().catch(() => ({}));
  const detail = body?.detail;
  if (detail && typeof detail === "object") {
    throw new ApiRequestError(typeof detail.message === "string" ? detail.message : fallback, detail);
  }
  throw new ApiRequestError(typeof detail === "string" ? detail : fallback);
}

export const authSession = {
  getToken: () => sessionStorage.getItem(TOKEN_KEY),
  setToken: (token: string) => sessionStorage.setItem(TOKEN_KEY, token),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

export async function authFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  const token = authSession.getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401) {
    authSession.clear();
    window.dispatchEvent(new Event("auratwin:session-expired"));
  }
  return response;
}

export const api = {
  async login(email: string, password: string) {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Login failed");
    authSession.setToken(data.access_token);
    return data.user as { user_id: string; email: string; role: "ADMIN" | "OPERATOR"; building_ids: string[] };
  },
  async me() {
    const res = await authFetch(`${API_BASE}/auth/me`);
    if (!res.ok) throw new Error("Session expired");
    return res.json() as Promise<{ user_id: string; email: string; role: "ADMIN" | "OPERATOR"; building_ids: string[] }>;
  },
  logout() { authSession.clear(); },
  async getAccessUsers() {
    const res = await authFetch(`${API_BASE}/auth/access`);
    if (!res.ok) throw new Error("Unable to load access list");
    return (await res.json()).users as Array<{ user_id: string; email: string; role: "ADMIN" | "OPERATOR"; active?: boolean; building_ids: string[] }>;
  },
  async createOperator(email: string, password: string) {
    const res = await authFetch(`${API_BASE}/auth/operators`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to create operator");
    return data;
  },
  async revokeOperator(userId: string) {
    const res = await authFetch(`${API_BASE}/auth/operators/${userId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Unable to revoke operator access");
  },
  async getAuditRecords() {
    const res = await authFetch(`${API_BASE}/audit`);
    if (!res.ok) throw new Error("Unable to load audit records");
    return (await res.json()).records as Array<{ timestamp: string; user_id: string | null; role: string | null; action: string; resource: string; resource_id: string | null; building_id: string | null; success: boolean; metadata: Record<string, unknown> }>;
  },
  async getBuildings(): Promise<Array<{ building_id: string; building_key: string; organization_id: string; name: string }>> {
    const res = await authFetch(`${API_BASE}/buildings`);
    if (!res.ok) throw new Error("Unable to fetch authorized buildings");
    return (await res.json()).buildings;
  },

  async getIntegrations(buildingId: string) {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/integrations`);
    if (!res.ok) throw new Error("Unable to load building integrations");
    return (await res.json()).integrations as Array<any>;
  },
  async createIntegration(buildingId: string, payload: { name: string; integration_type: string; configuration: Record<string, unknown> }) {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/integrations`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to create integration");
    return data;
  },
  async updateIntegration(integrationId: string, payload: Record<string, unknown>) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to update integration");
    return data;
  },
  async disableIntegration(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to disable integration");
    return data;
  },
  async testIntegration(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/test-connection`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to validate integration configuration");
    return data;
  },
  async getIntegrationDevices(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/devices`);
    if (!res.ok) throw new Error("Unable to load devices");
    return (await res.json()).devices as Array<any>;
  },
  async createIntegrationDevice(integrationId: string, payload: Record<string, unknown>) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/devices`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to add device");
    return data;
  },
  async updateIntegrationDevice(deviceId: string, payload: Record<string, unknown>) {
    const res = await authFetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to update device");
    return data;
  },
  async disableIntegrationDevice(deviceId: string) {
    const res = await authFetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to disable device");
    return data;
  },
  async getDevicePoints(deviceId: string) {
    const res = await authFetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}/points`);
    if (!res.ok) throw new Error("Unable to load logical signal mappings");
    return (await res.json()).points as Array<any>;
  },
  async getPointLatestObservation(pointId: string) {
    const res = await authFetch(`${API_BASE}/point-mappings/${encodeURIComponent(pointId)}/latest-observation`);
    if (!res.ok) throw new Error("Unable to load latest mapped observation");
    return (await res.json()).observation as Record<string, unknown> | null;
  },
  async createSimulatedPointObservation(pointId: string, value: number, observedAt: string) {
    const res = await authFetch(`${API_BASE}/point-mappings/${encodeURIComponent(pointId)}/simulated-observation`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value, observed_at: observedAt }),
    });
    if (!res.ok) await throwApiError(res, "Simulated observation was rejected");
    return res.json() as Promise<{ accepted: boolean; persisted: boolean; runtime_input_applied: boolean; reason_code: string | null }>;
  },
  async createDevicePoint(deviceId: string, payload: Record<string, unknown>) {
    const res = await authFetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}/points`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to add point mapping");
    return data;
  },
  async decidePointMapping(pointId: string, decision: "confirm" | "reject") {
    const res = await authFetch(`${API_BASE}/point-mappings/${encodeURIComponent(pointId)}/${decision}`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to update mapping");
    return data;
  },
  async getZones(buildingId?: string): Promise<Zone[]> {
    const query = buildingId ? `?building_id=${encodeURIComponent(buildingId)}` : "";
    const res = await authFetch(`${API_BASE}/zones${query}`);
    if (!res.ok) throw new Error("Failed to fetch zones");
    const data = await res.json();
    return data.zones;
  },

  async getFloors(buildingId: string): Promise<Floor[]> {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/floors`);
    if (!res.ok) throw new Error("Failed to fetch floors for authorized building");
    return (await res.json()).floors as Floor[];
  },

  async getHistoricalTelemetry(params: {
    buildingId: string; floorId?: string; zoneId?: string; signal?: TelemetrySignal;
    startTime: string; endTime: string; limit?: number;
  }): Promise<TelemetryAnalyticsResponse> {
    const query = new URLSearchParams({ start_time: params.startTime, end_time: params.endTime });
    if (params.floorId) query.set("floor_id", params.floorId);
    if (params.zoneId) query.set("zone_id", params.zoneId);
    if (params.signal) query.set("signal", params.signal);
    if (params.limit) query.set("limit", String(params.limit));
    const res = await authFetch(`${API_BASE}/telemetry/buildings/${encodeURIComponent(params.buildingId)}?${query}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to load historical telemetry");
    return data as TelemetryAnalyticsResponse;
  },

  async getZoneState(zoneId: string): Promise<ZoneState> {
    const res = await authFetch(`${API_BASE}/zones/${zoneId}/state`);
    if (!res.ok) throw new Error(`Failed to fetch state for zone ${zoneId}`);
    return res.json();
  },

  async setManualOverride(zoneId: string, enabled: boolean) {
    const res = await authFetch(`${API_BASE}/zones/${zoneId}/manual-override`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) await throwApiError(res, "Unable to change manual override");
    const data = await res.json();
    return data;
  },

  async setZoneControlEnabled(zoneId: string, enabled: boolean) {
    const res = await authFetch(`${API_BASE}/zones/${zoneId}/control-enabled`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) await throwApiError(res, "Unable to change control state");
    const data = await res.json();
    return data;
  },

  async getZoneHistory(zoneId: string): Promise<SystemEvent[]> {
    const res = await authFetch(`${API_BASE}/zones/${zoneId}/history`);
    if (!res.ok) throw new Error(`Failed to fetch history for zone ${zoneId}`);
    const data = await res.json();
    return data.history;
  },

  async generateRecommendation(zoneId: string): Promise<RecommendationDecision> {
    const res = await authFetch(`${API_BASE}/zones/${zoneId}/recommendation`, {
      method: "POST"
    });
    if (!res.ok) throw new Error(`Failed to generate recommendation for zone ${zoneId}`);
    return res.json();
  },

  async applyControl(zoneId: string, decision: RecommendationDecision): Promise<ControlResult> {
    const res = await authFetch(`${API_BASE}/zones/${zoneId}/control`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(decision)
    });
    if (!res.ok) throw new Error(`Failed to apply control for zone ${zoneId}`);
    const data = await res.json();
    return data.control_result as ControlResult;
  },

  async getOccupancyStatus(): Promise<OccupancyStatus> {
    const res = await authFetch(`${API_BASE}/occupancy/status`);
    if (!res.ok) throw new Error("Failed to fetch occupancy status");
    return res.json();
  },

  async detectOccupancy(file: File, zoneId: string): Promise<OccupancyDetectionResponse> {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("zone_id", zoneId);

    const res = await authFetch(`${API_BASE}/occupancy/detect`, {
      method: "POST",
      body: formData
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Unknown error" }));
        throw new Error(err.detail || "Detection failed");
    }
    return res.json();
  },

  async getMonitoringStatus(): Promise<MonitoringStatus> {
    const res = await authFetch(`${API_BASE}/monitoring/status`);
    if (!res.ok) throw new Error("Failed to fetch monitoring status");
    return res.json();
  },

  async startMonitoring(): Promise<void> {
    const res = await authFetch(`${API_BASE}/monitoring/start`, { method: "POST" });
    if (!res.ok) throw new Error("Failed to start monitoring");
  },

  async stopMonitoring(): Promise<void> {
    const res = await authFetch(`${API_BASE}/monitoring/stop`, { method: "POST" });
    if (!res.ok) throw new Error("Failed to stop monitoring");
  },
  async getDemoStatus(): Promise<DemoStatus> {
    const res = await authFetch(`${API_BASE}/demo/status`);
    if (!res.ok) throw new Error("Failed to fetch demo status");
    return res.json();
  },
  async demoAction(action: "start" | "pause" | "resume" | "stop" | "reset"): Promise<DemoStatus> {
    const res = await authFetch(`${API_BASE}/demo/${action}`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Failed to ${action} demo`);
    return data;
  },
  async setDemoSpeed(speed: number): Promise<DemoStatus> {
    const res = await authFetch(`${API_BASE}/demo/speed`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed_multiplier: speed }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Failed to update demo speed");
    return data;
  },
  async getDemoBuildingSummary(): Promise<DemoBuildingSummary> {
    const res = await authFetch(`${API_BASE}/demo/building-summary`);
    if (!res.ok) throw new Error("Failed to fetch building summary");
    return res.json();
  },
  async getDemoActivity(): Promise<SystemEvent[]> {
    const res = await authFetch(`${API_BASE}/demo/activity`);
    if (!res.ok) throw new Error("Failed to fetch demo control activity");
    const data = await res.json();
    return data.activities as SystemEvent[];
  },
  async getDemoEvents(): Promise<SystemEvent[]> {
    const res = await authFetch(`${API_BASE}/demo/events`);
    if (!res.ok) throw new Error("Failed to fetch demo event history");
    const data = await res.json();
    return data.events as SystemEvent[];
  }
};

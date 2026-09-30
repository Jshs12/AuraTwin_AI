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
} from "../types/api";

// Keep browser clients attached to the machine serving the UI (localhost,
// loopback IP, or a LAN hostname) instead of the visitor's localhost.
export const API_BASE = `${window.location.protocol}//${window.location.hostname}:8000/api`;
const TOKEN_KEY = "auratwin_access_token";

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
  async getZones(): Promise<Zone[]> {
    const res = await authFetch(`${API_BASE}/zones`);
    if (!res.ok) throw new Error("Failed to fetch zones");
    const data = await res.json();
    return data.zones;
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
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to change manual override");
    return data;
  },

  async setZoneControlEnabled(zoneId: string, enabled: boolean) {
    const res = await authFetch(`${API_BASE}/zones/${zoneId}/control-enabled`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to change control state");
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

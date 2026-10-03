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
  status?: number;
  endpoint?: string;
  authenticationState: "authenticated" | "expired" | "anonymous";
  constructor(message: string, detail?: Record<string, unknown>, status?: number, endpoint?: string,
              authenticationState: "authenticated" | "expired" | "anonymous" = "authenticated") {
    const diagnostic = status == null ? "" : ` (HTTP ${status}${endpoint ? ` · ${endpoint}` : ""} · auth ${authenticationState})`;
    super(`${message}${diagnostic}`);
    this.name = "ApiRequestError";
    this.status = status;
    this.endpoint = endpoint;
    this.authenticationState = authenticationState;
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
    throw new ApiRequestError(typeof detail.message === "string" ? detail.message : fallback, detail,
      response.status, new URL(response.url).pathname);
  }
  throw new ApiRequestError(typeof detail === "string" ? detail : fallback, undefined,
    response.status, new URL(response.url).pathname);
}

let expiredToken: string | null = null;
export const authSession = {
  getToken: () => sessionStorage.getItem(TOKEN_KEY),
  setToken: (token: string) => { expiredToken = null; sessionStorage.setItem(TOKEN_KEY, token); },
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
  expire: (token: string, endpoint: string) => {
    if (authSession.getToken() !== token) return;
    authSession.clear();
    if (expiredToken === token) return;
    expiredToken = token;
    window.dispatchEvent(new CustomEvent("auratwin:session-expired", { detail: { status: 401, endpoint } }));
  },
};

export async function authFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const rawUrl = input instanceof Request ? input.url : input.toString();
  const endpoint = new URL(rawUrl, window.location.href).pathname;
  const token = authSession.getToken();
  if (!token) throw new ApiRequestError("Authentication required. Sign in to continue.", undefined, 401, endpoint, "anonymous");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(input, { ...init, headers });
  if (!response.ok) {
    const body = await response.clone().json().catch(() => ({}));
    const detail = body?.detail;
    const detailObject = detail && typeof detail === "object" && !Array.isArray(detail) ? detail as Record<string, unknown> : undefined;
    const message = typeof detailObject?.message === "string" ? detailObject.message
      : typeof detail === "string" ? detail
      : `Request failed with HTTP ${response.status}.`;
    const authState = response.status === 401 ? "expired" : "authenticated";
    if (response.status === 401) authSession.expire(token, endpoint);
    throw new ApiRequestError(message, detailObject, response.status, endpoint, authState);
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
  async getBuildings(): Promise<Array<{ building_id: string; building_key: string; organization_id: string; name: string; slug: string }>> {
    const res = await authFetch(`${API_BASE}/buildings`);
    if (!res.ok) throw new Error("Unable to fetch authorized buildings");
    return (await res.json()).buildings;
  },
  async getOrganizations(): Promise<Array<{ organization_id: string; name: string; slug: string }>> {
    const res = await authFetch(`${API_BASE}/organizations`);
    if (!res.ok) throw new Error("Unable to load authorized organizations");
    return (await res.json()).organizations;
  },
  async createBuilding(payload: { organization_id: string; name: string; slug: string; timezone: string }) {
    const res = await authFetch(`${API_BASE}/buildings`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) await throwApiError(res, "Unable to create building");
    return res.json();
  },
  async createFloor(buildingId: string, payload: { name: string; floor_key: string; level_number?: number }) {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/floors`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) await throwApiError(res, "Unable to create floor");
    return res.json();
  },
  async createZone(floorId: string, payload: Record<string, unknown>) {
    const res = await authFetch(`${API_BASE}/floors/${encodeURIComponent(floorId)}/zones`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) await throwApiError(res, "Unable to create zone");
    return res.json();
  },
  async getCommandPolicyStatus() {
    const res = await authFetch(`${API_BASE}/control/policy-status`);
    if (!res.ok) throw new Error("Unable to load control policy readiness");
    return res.json() as Promise<{ ready: boolean; missing_configuration: string[]; invalid_configuration: string[]; reason_code: string | null }>;
  },

  async getKnowledgeDocuments(buildingId: string) {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/knowledge/documents`);
    if (!res.ok) await throwApiError(res, "Unable to load building knowledge.");
    return (await res.json()).documents as KnowledgeDocument[];
  },
  async registerKnowledgeDocument(buildingId: string, payload: {
    name: string; category: string; file: File; description?: string;
    source_reference?: string; floor_id?: string; zone_id?: string; simulated: boolean;
  }) {
    const form = new FormData();
    form.set("name", payload.name); form.set("category", payload.category);
    form.set("file", payload.file); form.set("simulated", String(payload.simulated));
    if (payload.description) form.set("description", payload.description);
    if (payload.source_reference) form.set("source_reference", payload.source_reference);
    if (payload.floor_id) form.set("floor_id", payload.floor_id);
    if (payload.zone_id) form.set("zone_id", payload.zone_id);
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/knowledge/documents`, { method: "POST", body: form });
    if (!res.ok) await throwApiError(res, "Unable to register the knowledge document.");
    return await res.json() as KnowledgeDocument;
  },
  async ingestKnowledgeDocument(buildingId: string, documentId: string) {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/knowledge/documents/${encodeURIComponent(documentId)}/ingest`, { method: "POST" });
    if (!res.ok) await throwApiError(res, "Unable to ingest the knowledge document.");
    return await res.json() as { document_status: string; ingestion_status: string; ingestion_error_code?: string | null; chunks: number; pages?: number | null; version: number };
  },
  async addKnowledgeVersion(buildingId: string, documentId: string, file: File) {
    const form = new FormData(); form.set("file", file);
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/knowledge/documents/${encodeURIComponent(documentId)}/versions`, { method: "POST", body: form });
    if (!res.ok) await throwApiError(res, "Unable to register a new document version.");
    return await res.json() as KnowledgeDocument;
  },
  async updateKnowledgeDocument(buildingId: string, documentId: string, payload: Record<string, unknown>) {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/knowledge/documents/${encodeURIComponent(documentId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!res.ok) await throwApiError(res, "Unable to update document details.");
    return await res.json() as KnowledgeDocument;
  },
  async archiveKnowledgeDocument(buildingId: string, documentId: string) {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/knowledge/documents/${encodeURIComponent(documentId)}/archive`, { method: "POST" });
    if (!res.ok) await throwApiError(res, "Unable to archive document.");
    return await res.json() as { document_id: string; management_status: string };
  },
  async retrieveKnowledge(buildingId: string, query: string, top_k = 5) {
    const res = await authFetch(`${API_BASE}/buildings/${encodeURIComponent(buildingId)}/knowledge/retrieve`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query, top_k }),
    });
    if (!res.ok) await throwApiError(res, "Unable to search building knowledge.");
    return await res.json() as { provider: string; semantic_search: boolean; control_authority: string; results: KnowledgeResult[] };
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
  async connectIntegration(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/connect`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail?.message || data.detail?.code || "Unable to test adapter connection");
    return data;
  },
  async disconnectIntegration(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/disconnect`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail?.message || data.detail?.code || "Unable to disconnect adapter");
    return data;
  },
  async pollIntegration(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/poll`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail?.message || data.detail?.code || "Unable to poll read-only adapter");
    return data;
  },
  async discoverDevicePoints(deviceId: string) {
    const res = await authFetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}/discover-points`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail?.message || data.detail?.code || "Unable to discover read-only points");
    return data;
  },
  async discoverIntegration(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/discover`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Unable to request simulated discovery");
    return data;
  },
  async getIntegrationCommissioning(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/commissioning`);
    if (!res.ok) throw new Error("Unable to load commissioning status");
    return res.json();
  },
  async evaluateIntegrationCommissioning(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/commissioning/evaluate`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to evaluate commissioning");
    return data;
  },
  async getIntegrationHealth(integrationId: string) {
    const res = await authFetch(`${API_BASE}/integrations/${encodeURIComponent(integrationId)}/health`);
    if (!res.ok) throw new Error("Unable to load integration health");
    return res.json();
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
  async updateDevicePoint(pointId: string, payload: Record<string, unknown>) {
    const res = await authFetch(`${API_BASE}/point-mappings/${encodeURIComponent(pointId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to update point mapping");
    return data;
  },
  async deactivatePointMapping(pointId: string) {
    const res = await authFetch(`${API_BASE}/point-mappings/${encodeURIComponent(pointId)}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Unable to deactivate point mapping");
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
    if (!res.ok) await throwApiError(res, "Unable to load zones for this building.");
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
    if (!res.ok) await throwApiError(res, "Unable to load this zone’s current state.");
    return res.json();
  },

  async getOptimizationIntervals(zoneId: string) {
    const res = await authFetch(`${API_BASE}/zones/${encodeURIComponent(zoneId)}/optimization-intervals`);
    if (!res.ok) await throwApiError(res, "Unable to load optimization interval status.");
    return res.json() as Promise<{ active: OptimizationInterval | null; completed: OptimizationInterval[]; persistence: "DATABASE" }>;
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
    if (!res.ok) await throwApiError(res, "Recommendation could not be generated.");
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
    if (!res.ok) await throwApiError(res, "The control command was rejected.");
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

export interface KnowledgeDocument {
  document_id: string; organization_id: string; building_id: string; floor_id: string | null; zone_id: string | null;
  name: string; category: string; description: string | null; source_reference: string | null;
  ingestion_status: string; management_status: string; simulated: boolean; created_at: string; updated_at: string;
  versions: Array<{ version_id: string; version: number; file_name: string; file_format: string;
    content_hash: string; ingestion_status: string; ingestion_error_code: string | null;
    provenance: string; is_active: boolean; pages: number | null; chunks: number; created_at: string }>;
}
export interface KnowledgeResult {
  document_id: string; document_name: string; chunk_id: string; text: string; score: number | null;
  page: number | null; section: string | null; building_id: string; source: string | null; version: number;
  provenance: string; simulated: boolean; category: string;
}

export interface OptimizationInterval {
  interval_id: string; organization_id: string | null; building_id: string | null;
  floor_id: string | null; database_zone_id: string | null; zone_id: string;
  started_at: string; ended_at: string | null;
  starting_occupancy: number; ending_occupancy: number | null; starting_temperature: number;
  ending_temperature: number | null; previous_setpoint: number; optimized_setpoint: number;
  starting_occupancy_observed_at: string | null; ending_occupancy_observed_at: string | null;
  setpoint_observed_at: string | null; starting_temperature_observed_at: string | null;
  ending_temperature_observed_at: string | null; duration_seconds: number | null;
  starting_energy_kwh: number | null; starting_energy_observed_at: string | null;
  ending_energy_kwh: number | null; ending_energy_observed_at: string | null;
  energy_unit: "kWh"; energy_status: "PENDING" | "AVAILABLE" | "UNAVAILABLE" | "INVALID";
  energy_reason_code: string | null; energy_source: string | null; energy_quality: string | null;
  energy_simulated: boolean | null;
  energy_consumed_kwh: number | null; cost_consumed: number | null;
  tariff_rate_per_kwh: number | null; currency: string | null;
  starting_tariff_observed_at: string | null; ending_tariff_observed_at: string | null;
  tariff_source: string | null; tariff_quality: string | null; tariff_simulated: boolean | null;
  cost_status: "PENDING" | "AVAILABLE" | "UNAVAILABLE" | "INVALID";
  cost_reason_code: string | null; quality_state: string;
  status: "ACTIVE" | "COMPLETED"; source: string; simulated: boolean;
  reason: string;
}

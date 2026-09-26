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

export const api = {
  async getZones(): Promise<Zone[]> {
    const res = await fetch(`${API_BASE}/zones`);
    if (!res.ok) throw new Error("Failed to fetch zones");
    const data = await res.json();
    return data.zones;
  },

  async getZoneState(zoneId: string): Promise<ZoneState> {
    const res = await fetch(`${API_BASE}/zones/${zoneId}/state`);
    if (!res.ok) throw new Error(`Failed to fetch state for zone ${zoneId}`);
    return res.json();
  },

  async getZoneHistory(zoneId: string): Promise<SystemEvent[]> {
    const res = await fetch(`${API_BASE}/zones/${zoneId}/history`);
    if (!res.ok) throw new Error(`Failed to fetch history for zone ${zoneId}`);
    const data = await res.json();
    return data.history;
  },

  async generateRecommendation(zoneId: string): Promise<RecommendationDecision> {
    const res = await fetch(`${API_BASE}/zones/${zoneId}/recommendation`, {
      method: "POST"
    });
    if (!res.ok) throw new Error(`Failed to generate recommendation for zone ${zoneId}`);
    return res.json();
  },

  async applyControl(zoneId: string, decision: RecommendationDecision): Promise<ControlResult> {
    const res = await fetch(`${API_BASE}/zones/${zoneId}/control`, {
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
    const res = await fetch(`${API_BASE}/occupancy/status`);
    if (!res.ok) throw new Error("Failed to fetch occupancy status");
    return res.json();
  },

  async detectOccupancy(file: File, zoneId: string): Promise<OccupancyDetectionResponse> {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("zone_id", zoneId);

    const res = await fetch(`${API_BASE}/occupancy/detect`, {
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
    const res = await fetch(`${API_BASE}/monitoring/status`);
    if (!res.ok) throw new Error("Failed to fetch monitoring status");
    return res.json();
  },

  async startMonitoring(): Promise<void> {
    const res = await fetch(`${API_BASE}/monitoring/start`, { method: "POST" });
    if (!res.ok) throw new Error("Failed to start monitoring");
  },

  async stopMonitoring(): Promise<void> {
    const res = await fetch(`${API_BASE}/monitoring/stop`, { method: "POST" });
    if (!res.ok) throw new Error("Failed to stop monitoring");
  },
  async getDemoStatus(): Promise<DemoStatus> {
    const res = await fetch(`${API_BASE}/demo/status`);
    if (!res.ok) throw new Error("Failed to fetch demo status");
    return res.json();
  },
  async demoAction(action: "start" | "pause" | "resume" | "stop" | "reset"): Promise<DemoStatus> {
    const res = await fetch(`${API_BASE}/demo/${action}`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Failed to ${action} demo`);
    return data;
  },
  async setDemoSpeed(speed: number): Promise<DemoStatus> {
    const res = await fetch(`${API_BASE}/demo/speed`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speed_multiplier: speed }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Failed to update demo speed");
    return data;
  },
  async getDemoBuildingSummary(): Promise<DemoBuildingSummary> {
    const res = await fetch(`${API_BASE}/demo/building-summary`);
    if (!res.ok) throw new Error("Failed to fetch building summary");
    return res.json();
  },
  async getDemoActivity(): Promise<SystemEvent[]> {
    const res = await fetch(`${API_BASE}/demo/activity`);
    if (!res.ok) throw new Error("Failed to fetch demo control activity");
    const data = await res.json();
    return data.activities as SystemEvent[];
  },
  async getDemoEvents(): Promise<SystemEvent[]> {
    const res = await fetch(`${API_BASE}/demo/events`);
    if (!res.ok) throw new Error("Failed to fetch demo event history");
    const data = await res.json();
    return data.events as SystemEvent[];
  }
};

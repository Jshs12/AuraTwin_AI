import type { SystemEvent } from "../../types/api";
import { formatISTTimestamp } from "../../utils/time";

interface EventStreamProps {
  events: SystemEvent[];
  connectionStatus?: string;
}

const eventColors: Record<string, string> = {
  SNAPSHOT_CAPTURED: "#4a9eff",
  SCENE_CHANGED: "#f0a050",
  YOLO_DETECTION: "#6cde7c",
  OCCUPANCY_CHANGED: "#cc77ff",
  OCCUPANCY_DETECTED: "#cc77ff",
  SIMULATED_OCCUPANCY_INPUT: "#cc77ff",
  DEMO_PHASE_CHANGED: "#f0a050",
  DEMO_CONTROL_ACTIVITY: "#6cde7c",
  DEMO_SCENARIO_COMPLETED: "#6cde7c",
  STATE_EVALUATED: "#4a9eff",
  OPTIMIZATION_REQUESTED: "#f0a050",
  OPTIMIZATION_RECOMMENDATION: "#6cde7c",
  RECOMMENDATION_VALIDATED: "#6cde7c",
  RECOMMENDATION_REJECTED: "#ff6b6b",
  FALLBACK_ACTIVATED: "#f0a050",
  CONTROL_COMMAND_REQUESTED: "#f0a050",
  CONTROL_VALIDATION: "#4a9eff",
  CONTROL_COMMAND_SENT: "#4a9eff",
  CONTROL_ACKNOWLEDGED: "#6cde7c",
  CONTROL_COMMAND: "#ff6b6b",
  ENERGY_UPDATE: "#4a9eff",
  HVAC_RESPONSE: "#6cde7c",
};

function getPayloadSummary(event: SystemEvent): string {
  const p = event.payload as Record<string, unknown>;
  if (event.event_type === "SNAPSHOT_CAPTURED" && typeof p.size === "number")
    return `${p.size} bytes`;
  if (event.event_type === "SCENE_CHANGED" && typeof p.score === "number")
    return `score: ${(p.score as number).toFixed(1)}`;
  if (event.event_type === "YOLO_DETECTION" && typeof p.people_count === "number")
    return `${p.people_count} people · ${(p.processing_time_ms as number).toFixed(0)}ms`;
  if (event.event_type === "OCCUPANCY_CHANGED")
    return `${p.previous_count} → ${p.current_count} (Δ${(p.delta as number) > 0 ? "+" : ""}${p.delta})`;
  if (event.event_type === "SIMULATED_OCCUPANCY_INPUT")
    return `${p.people_count} people · DEMO SIMULATION`;
  if (event.event_type === "DEMO_PHASE_CHANGED")
    return `${p.phase} · phase ${p.phase_number}/${p.total_phases}`;
  if (event.event_type === "DEMO_CONTROL_ACTIVITY")
    return `${p.previous_setpoint ?? "—"}°C → ${p.applied_setpoint ?? "—"}°C · ${p.success ? "ACKNOWLEDGED" : "FAILED"}`;
  if (event.event_type === "ENERGY_UPDATE" && typeof p.power_kw === "number")
    return `${(p.power_kw as number).toFixed(2)} kW`;
  if (event.event_type === "OPTIMIZATION_RECOMMENDATION" && typeof p.recommended_setpoint === "number")
    return `→ ${p.recommended_setpoint}°C`;
  if (event.event_type === "CONTROL_COMMAND_REQUESTED" && typeof p.requested_setpoint === "number")
    return `requested → ${p.requested_setpoint}°C`;
  if (event.event_type === "CONTROL_VALIDATION" && typeof p.outcome === "string")
    return `safety: ${p.outcome}`;
  if (event.event_type === "CONTROL_ACKNOWLEDGED" && typeof p.acknowledged === "boolean")
    return p.acknowledged ? `applied ${p.applied_setpoint}°C` : `not acknowledged · ${p.error_code ?? "failed"}`;
  if (event.event_type === "HVAC_RESPONSE" && typeof p.hvac_mode === "string")
    return `${p.hvac_mode} · ${p.power_kw ?? 0} kW`;
  return Object.entries(p).slice(0, 2).map(([k, v]) => `${k}: ${v}`).join(" · ");
}

export function EventStream({ events, connectionStatus = "disconnected" }: EventStreamProps) {
  const live = connectionStatus === "connected";
  if (events.length === 0) {
    return (
      <div className="card">
        <div className="card-title">LIVE EVENT STREAM</div>
        <div className={`badge ${live ? "success" : "warning"}`} role="status">EVENT CONNECTION · {connectionStatus.replace(/-/g, " ").toUpperCase()}</div>
        <div style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: "0.5rem", textAlign: "center", padding: "2rem" }}>
          No events yet — start monitoring to see the live stream.
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
        <div className="card-title" style={{ marginBottom: 0 }}>LIVE EVENT STREAM</div>
        <span className={`badge ${live ? "success" : "warning"}`} style={{ fontSize: "0.7rem" }}>● {live ? "LIVE" : connectionStatus.replace(/-/g, " ").toUpperCase()} · {events.length} events</span>
      </div>
      <div style={{ maxHeight: 420, overflowY: "auto", display: "flex", flexDirection: "column", gap: "0.3rem" }}>
        {events.map(event => {
          const color = eventColors[event.event_type] ?? "var(--text-secondary)";
          const time = formatISTTimestamp(event.timestamp);
          return (
            <div key={event.event_id} style={{
              display: "grid",
              gridTemplateColumns: "60px 140px 1fr auto",
              gap: "0.5rem",
              alignItems: "center",
              padding: "0.3rem 0.5rem",
              background: "var(--surface-elevated)",
              borderRadius: 5,
              borderLeft: `3px solid ${color}`,
              fontSize: "0.75rem"
            }}>
              <div style={{ color: "var(--text-muted)", fontFamily: "monospace" }}>{time}</div>
              <div style={{ color, fontWeight: 600, fontSize: "0.7rem", letterSpacing: "0.02em" }}>
                {event.event_type}
              </div>
              <div style={{ color: "var(--text-secondary)", fontSize: "0.7rem" }}>
                {getPayloadSummary(event)}
              </div>
              <div style={{ color: "var(--text-muted)", fontSize: "0.65rem" }}>
                {event.zone_id.replace(/_/g, " ")}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

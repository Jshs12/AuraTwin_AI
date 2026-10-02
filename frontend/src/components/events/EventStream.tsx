import type { SystemEvent } from "../../types/api";
import { formatISTTimestamp } from "../../utils/time";
import { useMemo, useState } from "react";
import type { CSSProperties } from "react";

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
  PROVIDER_FAILURE: "#ff6b6b",
  PROVIDER_RECOVERY: "#f0a050",
  FAIL_SAFE_ACTIVATED: "#ff6b6b",
  AUTO_CONTROL_RESUMED: "#6cde7c",
  MANUAL_OVERRIDE_ENABLED: "#f0a050",
  MANUAL_OVERRIDE_DISABLED: "#4a9eff",
  CONTROL_ENABLED: "#6cde7c",
  CONTROL_DISABLED: "#f0a050",
  COMMAND_BLOCKED_BY_OVERRIDE: "#ff6b6b",
  COMMAND_BLOCKED_BY_CONTROL_DISABLE: "#ff6b6b",
  RUNTIME_OBSERVATION_APPLIED: "#6cde7c",
  RUNTIME_OBSERVATION_REJECTED: "#ff6b6b",
};

const eventGroups = ["All", "Occupancy", "Optimization", "HVAC", "Energy", "Safety", "System"] as const;
function eventGroup(type: string): typeof eventGroups[number] {
  if (/OCCUPANCY|YOLO|SNAPSHOT|SCENE/.test(type)) return "Occupancy";
  if (/OPTIMIZATION|RECOMMENDATION|FALLBACK|INTELLIGENCE/.test(type)) return "Optimization";
  if (/SAFETY|FAIL_SAFE|OVERRIDE|BLOCKED|PROVIDER_FAILURE/.test(type)) return "Safety";
  if (/ENERGY|TARIFF/.test(type)) return "Energy";
  if (/CONTROL|HVAC/.test(type)) return "HVAC";
  return "System";
}

function getPayloadSummary(event: SystemEvent): string {
  const p = event.payload as Record<string, unknown>;
  if (event.event_type === "OPTIMIZATION_COMPLETED") {
    const energy = typeof p.energy_consumed_kwh === "number" ? `${p.energy_consumed_kwh.toFixed(3)} kWh consumed` : "energy impact unavailable";
    return `${typeof p.duration_seconds === "number" ? `${Math.round(p.duration_seconds / 60)} min · ` : ""}${energy}${p.simulated === true ? " · SIMULATED" : ""}`;
  }
  if (event.event_type === "ENERGY_IMPACT_CALCULATED" && typeof p.energy_consumed_kwh === "number")
    return `${p.energy_consumed_kwh.toFixed(3)} kWh consumed${p.simulated === true ? " · SIMULATED" : ""}`;
  if (event.event_type === "ENERGY_IMPACT_UNAVAILABLE" || event.event_type === "IMPACT_UNAVAILABLE")
    return "Validated telemetry coverage unavailable; no impact value was estimated.";
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
  if (event.event_type === "RECOMMENDATION_REJECTED" || event.event_type === "CONTROL_REJECTED" || event.event_type === "COMMAND_BLOCKED_BY_OVERRIDE" || event.event_type === "COMMAND_BLOCKED_BY_CONTROL_DISABLE")
    return `Not applied · ${typeof p.reason === "string" ? p.reason : typeof p.reason_code === "string" ? p.reason_code.replaceAll("_", " ").toLowerCase() : "safety validation did not pass"}`;
  if (event.event_type === "CONTROL_ACKNOWLEDGED" && typeof p.acknowledged === "boolean")
    return p.acknowledged ? `applied ${p.applied_setpoint}°C` : `not acknowledged · ${p.error_code ?? "failed"}`;
  if (event.event_type === "HVAC_RESPONSE" && typeof p.hvac_mode === "string")
    return `${p.hvac_mode}${typeof p.power_kw === "number" ? ` · ${p.power_kw.toFixed(2)} kW` : ""}`;
  return typeof p.message === "string" ? p.message : "System state recorded.";
}

function readableEvent(type: string) {
  return type.toLowerCase().split("_").filter(Boolean).map(part => part[0].toUpperCase() + part.slice(1)).join(" ");
}

export function EventStream({ events, connectionStatus = "disconnected" }: EventStreamProps) {
  const [selectedGroup, setSelectedGroup] = useState<typeof eventGroups[number]>("All");
  const visibleEvents = useMemo(() => selectedGroup === "All" ? events : events.filter(event => eventGroup(event.event_type) === selectedGroup), [events, selectedGroup]);
  const live = connectionStatus === "connected";
  if (events.length === 0) {
    return (
      <div className="card">
        <div className="card-title">BUILDING ACTIVITY</div>
        <div className={`badge ${live ? "success" : "warning"}`} role="status">EVENT CONNECTION · {connectionStatus.replace(/-/g, " ").toUpperCase()}</div>
        <div style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: "0.5rem", textAlign: "center", padding: "2rem" }}>
          No events yet — start monitoring to see the live stream.
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="event-heading">
        <div><div className="card-title" style={{ marginBottom: 0 }}>BUILDING ACTIVITY</div><h2>Event timeline</h2></div>
        <span className={`badge ${live ? "success" : "warning"}`} style={{ fontSize: "0.7rem" }}>● {live ? "LIVE" : connectionStatus.replace(/-/g, " ").toUpperCase()} · {events.length} events</span>
      </div>
      <div className="event-filters" role="group" aria-label="Filter event groups">
        {eventGroups.map(group => <button type="button" key={group} aria-pressed={selectedGroup === group} onClick={() => setSelectedGroup(group)}>{group}</button>)}
      </div>
      <div className="event-timeline" aria-label="Recent event timeline">
        {visibleEvents.length === 0 ? <div className="product-empty-state">No {selectedGroup === "All" ? "events" : `${selectedGroup.toLowerCase()} events`} in this view.</div> : visibleEvents.map(event => {
          const color = eventColors[event.event_type] ?? "var(--text-secondary)";
          const time = formatISTTimestamp(event.timestamp);
          return (
            <article key={event.event_id} className="event-timeline-item" style={{ "--event-color": color } as CSSProperties}>
              <time dateTime={event.timestamp}>{time}</time>
              <i aria-hidden="true" />
              <div className="event-timeline-copy">
                <div className="event-timeline-title"><strong>{readableEvent(event.event_type)}</strong><span>{event.zone_id.replace(/_/g, " ")}</span></div>
                <p>{getPayloadSummary(event)}</p>
                <small>{event.source}{((event.payload as Record<string, unknown>).simulated === true) ? " · SIMULATED" : ""}</small>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

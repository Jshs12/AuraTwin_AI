import type { Zone } from "../../types/api";
import type { MonitoringZoneStatus } from "../../types/api";
import type { OptimizationInterval } from "../../services/api";

export function ZoneList({
  zones,
  selectedZoneId,
  onSelectZone,
  monitoringZones = [],
  optimizationIntervals = [],
}: {
  zones: Zone[];
  selectedZoneId: string | null;
  onSelectZone: (id: string) => void;
  monitoringZones?: MonitoringZoneStatus[];
  optimizationIntervals?: OptimizationInterval[];
}) {
  const monMap = Object.fromEntries(monitoringZones.map(z => [z.zone_id, z]));
  const optimizationMap = Object.fromEntries(optimizationIntervals.map(item => [item.zone_id, item]));

  return (
    <div className="zone-grid">
      {zones.map((zone) => {
        const mon = monMap[zone.zone_id];
        const optimization = optimizationMap[zone.zone_id];
        const isMonitored = !!mon;
        const people = mon?.last_snapshot ? mon.last_people_count : zone.current_occupancy;
        const utilization = zone.capacity > 0 && typeof people === "number"
          ? Math.min(100, Math.max(0, people / zone.capacity * 100)) : 0;
        const stateColor = mon?.status === "MONITORING" ? "var(--success)"
          : mon?.status?.includes("COOLDOWN") ? "var(--accent)"
          : mon?.status?.includes("ERROR") ? "var(--warning)"
          : "var(--border)";

        return (
          <button
            type="button"
            key={zone.zone_id}
            className={`card zone-card ${selectedZoneId === zone.zone_id ? "active" : ""}`}
            onClick={() => onSelectZone(zone.zone_id)}
            aria-label={`Select zone ${zone.name}`}
            style={{ borderTop: `3px solid ${isMonitored ? stateColor : "var(--border)"}` }}
          >
            <div className="zone-header">
              <div>
                <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>{zone.name}</div>
                <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", textTransform: "uppercase" }}>{zone.type}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <span className={`badge ${optimization ? "success" : isMonitored ? "primary" : "neutral"}`}>
                  {optimization ? "OPTIMIZING" : isMonitored ? "MONITORING" : "READY"}
                </span>
              </div>
            </div>
            {isMonitored && (
              <div style={{ fontSize: "0.65rem", color: stateColor, marginTop: "0.25rem" }}>
                {mon.status}{mon.cooldown_remaining_seconds > 0 ? ` · ${Math.ceil(mon.cooldown_remaining_seconds)}s` : ""}
              </div>
            )}
            <div className="zone-card-metrics">
              <span>Occupancy <b>{people ?? "—"} / {zone.capacity} people</b></span>
              <div className="bar-track" aria-label={`${utilization.toFixed(0)} percent capacity`}><i style={{ width: `${utilization}%` }} /></div>
              <span>Temperature <b>{zone.current_temperature == null ? "—" : `${zone.current_temperature.toFixed(1)}°C`}</b></span>
              <span>HVAC setpoint <b>{zone.current_setpoint == null ? "—" : `${zone.current_setpoint.toFixed(1)}°C`}</b></span>
              <span>Utilization <b>{people == null ? "No current sample" : `${utilization.toFixed(0)}%`}</b></span>
              <span>Optimization <b>{optimization ? `${Math.max(0, Math.floor((Date.now() - Date.parse(optimization.started_at)) / 60000))} min · holding` : "No active interval"}</b></span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

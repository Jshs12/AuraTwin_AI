import type { Zone } from "../../types/api";
import type { MonitoringZoneStatus } from "../../types/api";

export function ZoneList({
  zones,
  selectedZoneId,
  onSelectZone,
  monitoringZones = [],
}: {
  zones: Zone[];
  selectedZoneId: string | null;
  onSelectZone: (id: string) => void;
  monitoringZones?: MonitoringZoneStatus[];
}) {
  const monMap = Object.fromEntries(monitoringZones.map(z => [z.zone_id, z]));

  return (
    <div className="zone-grid" style={{ flexDirection: "column", display: "flex", gap: "0.5rem" }}>
      {zones.map((zone) => {
        const mon = monMap[zone.zone_id];
        const isMonitored = !!mon;
        const people = mon?.last_people_count ?? zone.current_occupancy;
        const utilization = zone.capacity > 0 ? Math.min(100, Math.max(0, people / zone.capacity * 100)) : 0;
        const stateColor = mon?.status === "MONITORING" ? "var(--success)"
          : mon?.status?.includes("COOLDOWN") ? "var(--accent)"
          : mon?.status?.includes("ERROR") ? "var(--warning)"
          : "var(--border)";

        return (
          <div
            key={zone.zone_id}
            className={`card zone-card ${selectedZoneId === zone.zone_id ? "active" : ""}`}
            onClick={() => onSelectZone(zone.zone_id)}
            role="button"
            tabIndex={0}
            aria-label={`Select zone ${zone.name}`}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelectZone(zone.zone_id)}
            style={{ borderLeft: `3px solid ${isMonitored ? stateColor : "var(--border)"}` }}
          >
            <div className="zone-header">
              <div>
                <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>{zone.name}</div>
                <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", textTransform: "uppercase" }}>{zone.type}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                {isMonitored && mon.last_people_count !== undefined ? (
                  <>
                    <div style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                      {mon.last_people_count}
                    </div>
                    <div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>people</div>
                  </>
                ) : (
                  <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>cap: {zone.capacity}</div>
                )}
              </div>
            </div>
            {isMonitored && (
              <div style={{ fontSize: "0.65rem", color: stateColor, marginTop: "0.25rem" }}>
                {mon.status} {mon.cooldown_remaining_seconds > 0 ? `· ${mon.cooldown_remaining_seconds}s` : ""}
              </div>
            )}
            <div className="zone-card-metrics">
              <span>Occupancy <b>{people} / {zone.capacity}</b></span>
              <div className="bar-track"><i style={{ width: `${utilization}%` }} /></div>
              <span>Temperature <b>{zone.current_temperature == null ? "—" : `${zone.current_temperature.toFixed(1)}°C`}</b></span>
              <span>HVAC setpoint <b>{zone.current_setpoint == null ? "—" : `${zone.current_setpoint.toFixed(1)}°C`}</b></span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

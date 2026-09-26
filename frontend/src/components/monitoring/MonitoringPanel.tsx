import type { MonitoringStatus } from "../../types/api";
import { formatISTTimestamp } from "../../utils/time";

interface MonitoringPanelProps {
  status: MonitoringStatus | null;
  error?: string | null;
  connected: boolean;
  onStart: () => void;
  onStop: () => void;
}

export function MonitoringPanel({ status, error, connected, onStart, onStop }: MonitoringPanelProps) {
  const running = status?.running ?? false;
  const zones = status?.zones ?? [];
  const formatTime = (iso: string | null) => {
    if (!iso) return "—";
    return formatISTTimestamp(iso);
  };

  const stateColor = (s: string) => {
    if (s === "MONITORING") return "var(--success)";
    if (s === "MONITORING (COOLDOWN)") return "var(--accent)";
    if (s === "MONITORING (NO CHANGE)") return "var(--text-muted)";
    if (s.startsWith("ERROR")) return "var(--warning)";
    if (s === "IDLE") return "var(--text-muted)";
    return "var(--accent)";
  };

  return (
    <div className="card monitoring-panel">
      {error && <div className="demo-error" role="alert">{error}</div>}
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
        <div>
          <div className="card-title" style={{ marginBottom: 0 }}>AUTONOMOUS MONITORING</div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.25rem" }}>
            <span style={{
              width: 8, height: 8, borderRadius: "50%", display: "inline-block",
              background: running ? "var(--success)" : "var(--text-muted)",
              boxShadow: running ? "0 0 8px var(--success)" : "none",
              animation: running ? "pulse 2s infinite" : "none"
            }} />
            <span style={{ fontSize: "0.8rem", color: running ? "var(--success)" : "var(--text-muted)", fontWeight: 600, letterSpacing: "0.05em" }}>
              {running ? "RUNNING" : "STOPPED"}
            </span>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginLeft: "0.5rem" }}>
              WS: {connected ? "●" : "○"}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {running ? (
            <button className="btn btn-secondary" onClick={onStop} style={{ fontSize: "0.8rem", padding: "0.35rem 0.8rem" }}>
              ■ Stop Monitoring
            </button>
          ) : (
            <button className="btn btn-primary" onClick={onStart} style={{ fontSize: "0.8rem", padding: "0.35rem 0.8rem" }}>
              ▶ Start Monitoring
            </button>
          )}
        </div>
      </div>

      {/* Zone rotation grid */}
      {status && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", marginBottom: "1rem" }}>
            <div className="stat-block">
              <div className="stat-label">Monitored</div>
              <div className="stat-value">{status.zones_enabled} / {status.zones_total}</div>
            </div>
            <div className="stat-block">
              <div className="stat-label">Camera</div>
              <div className="stat-value" style={{ fontSize: "0.85rem" }}>
                {status.demo_simulation ? "DEMO OCCUPANCY INPUT · SIMULATED" : `${status.camera_provider.toUpperCase()} · SIMULATED`}
              </div>
            </div>
          </div>

          <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.75rem" }}>
            <div className="card-title" style={{ fontSize: "0.7rem", marginBottom: "0.5rem" }}>ZONE STATUS</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
              {zones.map(z => {
                const isCooldown = z.status.includes("COOLDOWN");
                return (
                  <div key={z.zone_id} style={{
                    display: "grid", gridTemplateColumns: "1fr auto auto auto",
                    gap: "0.5rem", alignItems: "center",
                    padding: "0.35rem 0.5rem",
                    background: "var(--surface-elevated)",
                    borderRadius: 6,
                    borderLeft: `3px solid ${stateColor(z.status)}`
                  }}>
                    <div>
                      <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "var(--text-primary)" }}>
                        {z.zone_id.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase())}
                      </div>
                      <div style={{ fontSize: "0.68rem", color: stateColor(z.status) }}>{z.status}</div>
                    </div>
                    <div style={{ textAlign: "center" }}>
                      <div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>PEOPLE</div>
                      <div style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--text-primary)" }}>{z.last_people_count}</div>
                    </div>
                    {status.demo_simulation ? <>
                      <div style={{ textAlign: "center" }}><div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>SOURCE</div><div style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>DEMO</div></div>
                      <div style={{ textAlign: "center" }}><div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>PHASE</div><div style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>{z.phase ?? status.demo_phase ?? "—"}</div></div>
                    </> : <>
                      <div style={{ textAlign: "center" }}><div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>SNAP</div><div style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>{formatTime(z.last_snapshot)}</div></div>
                      <div style={{ textAlign: "center" }}><div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>COOLDOWN</div><div style={{ fontSize: "0.7rem", color: isCooldown ? "var(--accent)" : "var(--text-muted)" }}>{isCooldown && z.cooldown_remaining_seconds > 0 ? `${z.cooldown_remaining_seconds}s` : "—"}</div></div>
                    </>}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

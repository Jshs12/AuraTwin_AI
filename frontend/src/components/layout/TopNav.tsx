interface TopNavProps {
  activeSection: string;
  onNavigate: (s: string) => void;
  monitoringRunning: boolean;
  wsConnected: boolean;
  demoSimulation: boolean;
  occupancyProvider: "mock" | "yolo" | "unknown";
  occupancyProviderReady: boolean;
  role: "ADMIN" | "OPERATOR";
  systemOnline: boolean;
}

export function TopNav({ activeSection, onNavigate, monitoringRunning, wsConnected, demoSimulation, occupancyProvider, occupancyProviderReady, role, systemOnline }: TopNavProps) {
  const groups = [
    { label: "Operations", sections: ["Overview", "Zones", "Occupancy", "Energy", "Events"] },
    { label: "Intelligence", sections: ["Knowledge"] },
    { label: "Configuration", sections: [...(role === "OPERATOR" ? ["Integrations"] : []), "Access", ...(role === "ADMIN" ? ["Audit"] : [])] },
  ];
  const occupancyLabel = demoSimulation
    ? "DEMO SIMULATED"
    : occupancyProvider === "yolo"
      ? occupancyProviderReady ? "YOLO INFERENCE" : "YOLO NOT READY"
      : occupancyProvider === "mock" ? "MOCK SIMULATED" : "UNKNOWN";

  return (
    <nav className="top-nav" aria-label="Primary navigation">
      <div className="top-nav-main">
        <div className="brand">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="2" y="2" width="9" height="9" rx="1" fill="#1f6feb" />
            <rect x="13" y="2" width="9" height="9" rx="1" fill="#238636" />
            <rect x="2" y="13" width="9" height="9" rx="1" fill="#238636" />
            <rect x="13" y="13" width="9" height="9" rx="1" fill="#d29922" opacity="0.7" />
          </svg>
          AuraTwin AI
        </div>
        <div className="nav-groups">
          {groups.map(group => <div className="nav-group" key={group.label}>
            <span className="nav-group-label">{group.label}</span>
            <div className="nav-group-items">{group.sections.map((s) => (
            <button
              key={s}
              className={`nav-btn ${activeSection === s ? "nav-btn-active" : ""}`}
              onClick={() => onNavigate(s)}
              aria-current={activeSection === s ? "page" : undefined}
              style={{
                background: "none",
                border: "none",
                padding: "0.5rem 0.875rem",
                borderRadius: "var(--border-radius)",
                color: activeSection === s ? "var(--text-primary)" : "var(--text-secondary)",
                fontWeight: activeSection === s ? 600 : 400,
                fontSize: "0.875rem",
                cursor: "pointer",
                backgroundColor: activeSection === s ? "var(--bg-tertiary)" : "transparent",
                transition: "all 0.15s",
              }}
            >
              {s}
            </button>
            ))}</div>
          </div>)}
        </div>
      </div>

      <div className="nav-status">
        <span className={`system-online ${systemOnline ? "is-online" : "is-offline"}`}><i aria-hidden="true" />{systemOnline ? "SYSTEM ONLINE" : "STATUS UNAVAILABLE"}</span>
        {/* Monitoring status indicator */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{
            width: 8, height: 8, borderRadius: "50%", display: "inline-block",
            background: monitoringRunning ? "var(--success)" : "var(--text-muted)",
            boxShadow: monitoringRunning ? "0 0 8px var(--success)" : "none",
            animation: monitoringRunning ? "pulse 2s infinite" : "none",
          }} />
          <span style={{
            fontSize: "0.75rem",
            fontWeight: 600,
            color: monitoringRunning ? "var(--success)" : "var(--text-muted)",
          }}>
            {monitoringRunning ? "MONITORING" : "STOPPED"}
          </span>
          <span className={`connection-label ${wsConnected ? "connected" : ""}`} aria-label={`Event stream ${wsConnected ? "connected" : "disconnected"}`}>
            · EVENTS {wsConnected ? "LIVE" : "OFFLINE"}
          </span>
        </div>

        <div style={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          fontSize: "0.6875rem",
          color: "var(--text-muted)",
          borderLeft: "1px solid var(--border-color)",
          paddingLeft: "1rem",
        }}>
          <span>Occupancy <b>{occupancyLabel}</b></span>
          <span>Energy <b>SIMULATED</b></span>
          <span>HVAC <b>SIMULATED</b></span>
          <span>Optimizer <b>DETERMINISTIC</b></span>
        </div>
      </div>
    </nav>
  );
}

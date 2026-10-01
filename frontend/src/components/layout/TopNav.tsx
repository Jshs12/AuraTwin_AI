interface TopNavProps {
  activeSection: string;
  onNavigate: (s: string) => void;
  monitoringRunning: boolean;
  wsConnected: boolean;
  demoSimulation: boolean;
  occupancyProvider: "mock" | "yolo" | "unknown";
  occupancyProviderReady: boolean;
  role: "ADMIN" | "OPERATOR";
}

export function TopNav({ activeSection, onNavigate, monitoringRunning, wsConnected, demoSimulation, occupancyProvider, occupancyProviderReady, role }: TopNavProps) {
  const sections = ["Overview", "Zones", "Occupancy", "Energy", "Events", ...(role === "OPERATOR" ? ["Integrations"] : []), "Access", ...(role === "ADMIN" ? ["Audit"] : [])];
  const occupancyLabel = demoSimulation
    ? "DEMO SIMULATED"
    : occupancyProvider === "yolo"
      ? occupancyProviderReady ? "YOLO INFERENCE" : "YOLO NOT READY"
      : occupancyProvider === "mock" ? "MOCK SIMULATED" : "UNKNOWN";

  return (
    <nav className="top-nav">
      <div style={{ display: "flex", alignItems: "center", gap: "2rem" }}>
        <div className="brand">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="2" y="2" width="9" height="9" rx="1" fill="#1f6feb" />
            <rect x="13" y="2" width="9" height="9" rx="1" fill="#238636" />
            <rect x="2" y="13" width="9" height="9" rx="1" fill="#238636" />
            <rect x="13" y="13" width="9" height="9" rx="1" fill="#d29922" opacity="0.7" />
          </svg>
          AuraTwin AI
        </div>
        <div style={{ display: "flex", gap: "0.25rem" }}>
          {sections.map((s) => (
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
          ))}
        </div>
      </div>

      <div className="nav-status">
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
          <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
            · WS {wsConnected ? "●" : "○"}
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
          <span>Occupancy: <span style={{ color: "var(--accent-hover)" }}>{occupancyLabel}</span></span>
          <span>Energy: <span style={{ color: "var(--accent-orange)" }}>SIMULATED</span></span>
          <span>HVAC: <span style={{ color: "var(--accent-orange)" }}>SIMULATED</span></span>
          <span>Optimizer: <span style={{ color: "var(--text-secondary)" }}>DETERMINISTIC</span></span>
        </div>
      </div>
    </nav>
  );
}

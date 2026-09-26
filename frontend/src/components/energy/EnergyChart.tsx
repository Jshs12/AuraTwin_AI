import type { EnergyDataPoint } from "../../hooks/useMonitoring";

interface EnergyChartProps {
  history: EnergyDataPoint[];
  demoMode?: boolean;
}

export function EnergyChart({ history, demoMode = false }: EnergyChartProps) {
  const WIDTH = 100;
  const HEIGHT = 60;
  const points = history.slice(-120);
  const values = points.map(p => p.power_kw);
  const currentPower = values.at(-1) ?? 0;
  const avgPower = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
  const peakPower = values.length ? Math.max(...values) : 0;
  const minPower = Math.min(0, ...values);
  const maxPower = Math.max(1, ...values) * 1.08;
  const range = Math.max(maxPower - minPower, 1);
  const polyline = points.map((p, i) => {
    const x = (i / Math.max(points.length - 1, 1)) * WIDTH;
    const y = HEIGHT - ((p.power_kw - minPower) / range) * HEIGHT;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.75rem" }}>
        <div>
          <div className="card-title">{demoMode ? "DETERMINISTIC DEMO ENERGY" : "MOCK ENERGY STREAM"}</div>
          <div style={{ marginTop: "0.25rem" }}>
            <span className="badge warning">
              {demoMode
                ? "Power responds to simulated occupancy and HVAC · not meter data"
                : "Illustrative deterministic stream · not meter data"}
            </span>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.75rem", textAlign: "center" }}>
          <div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)", letterSpacing: "0.05em" }}>CURRENT</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--accent)" }}>{currentPower.toFixed(1)}</div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>kW</div>
          </div>
          <div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)", letterSpacing: "0.05em" }}>AVERAGE</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>{avgPower.toFixed(1)}</div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>kW</div>
          </div>
          <div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)", letterSpacing: "0.05em" }}>PEAK</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--warning-text, #f0a050)" }}>{peakPower.toFixed(1)}</div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>kW</div>
          </div>
        </div>
      </div>

      {/* SVG Chart */}
      <div style={{ background: "var(--surface-elevated)", borderRadius: 8, padding: "0.5rem", overflow: "hidden" }}>
        {points.length < 2 ? (
          <div style={{ height: 80, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: "0.8rem" }}>
            Waiting for energy data...
          </div>
        ) : (
          <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none" style={{ width: "100%", height: 80 }}>
            {/* Grid lines */}
            {[0, 0.25, 0.5, 0.75, 1].map(t => (
              <line key={t} x1={0} y1={HEIGHT * t} x2={WIDTH} y2={HEIGHT * t}
                stroke="var(--border)" strokeWidth="0.3" />
            ))}
            {/* Area fill */}
            <polyline
              points={`0,${HEIGHT} ${polyline} ${WIDTH},${HEIGHT}`}
              fill="var(--accent)"
              fillOpacity="0.12"
              stroke="none"
            />
            {/* Line */}
            <polyline
              points={polyline}
              fill="none"
              stroke="var(--accent)"
              strokeWidth="1.5"
              strokeLinejoin="round"
            />
            {points.map((point, index) => {
              const x = (index / Math.max(points.length - 1, 1)) * WIDTH;
              const y = HEIGHT - ((point.power_kw - minPower) / range) * HEIGHT;
              return <circle key={`${point.timestamp}-${index}`} cx={x} cy={y} r="1.2" fill="var(--accent)" opacity="0.8">
                <title>{`${point.time} · ${point.power_kw.toFixed(2)} kW${point.energy_kwh === undefined ? "" : ` · ${point.energy_kwh.toFixed(3)} kWh`}${point.occupancy === undefined ? "" : ` · ${point.occupancy} occupants`}`}</title>
              </circle>;
            })}
            {/* Current dot */}
            {points.length > 0 && (() => {
              const last = points[points.length - 1];
              const lx = WIDTH;
              const ly = HEIGHT - ((last.power_kw - minPower) / range) * HEIGHT;
              return <g><circle cx={lx} cy={ly} r="2.5" fill="var(--accent)" /><title>{`${last.time} · ${last.power_kw.toFixed(2)} kW`}</title></g>;
            })()}
          </svg>
        )}
      </div>

      {points.length >= 2 && <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.65rem", color: "var(--text-muted)", marginTop: "0.3rem" }}>
        <span>{points[0].time}</span><span>Power · kW</span><span>{points[points.length - 1].time}</span>
      </div>}

      <div style={{ marginTop: "0.5rem", fontSize: "0.65rem", color: "var(--text-muted)", textAlign: "right" }}>
        Rolling window: latest {points.length} {demoMode ? "building telemetry samples" : "illustrative zone samples"} · all metrics use this window
      </div>
    </div>
  );
}

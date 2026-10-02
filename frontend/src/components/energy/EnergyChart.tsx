import type { EnergyDataPoint } from "../../hooks/useMonitoring";
import { useState } from "react";

interface EnergyChartProps {
  history: EnergyDataPoint[];
  demoMode?: boolean;
}

export function EnergyChart({ history, demoMode = false }: EnergyChartProps) {
  const [windowSize, setWindowSize] = useState<30 | 60 | 120>(120);
  const WIDTH = 100;
  const HEIGHT = 60;
  const points = history.slice(-windowSize);
  const values = points.map(p => p.power_kw);
  const currentPower = values.at(-1);
  const avgPower = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
  const peakPower = values.length ? Math.max(...values) : 0;
  const minPower = Math.min(0, ...values);
  const maxPower = Math.max(1, ...values) * 1.08;
  const range = Math.max(maxPower - minPower, 1);
  const chartPoints = points.map((p, i) => {
    const x = (i / Math.max(points.length - 1, 1)) * WIDTH;
    const y = HEIGHT - ((p.power_kw - minPower) / range) * HEIGHT;
    return { x, y };
  });
  const linePath = chartPoints.reduce((path, point, index) => {
    if (index === 0) return `M ${point.x.toFixed(2)} ${point.y.toFixed(2)}`;
    const previous = chartPoints[index - 1];
    const middleX = ((previous.x + point.x) / 2).toFixed(2);
    const middleY = ((previous.y + point.y) / 2).toFixed(2);
    return `${path} Q ${previous.x.toFixed(2)} ${previous.y.toFixed(2)} ${middleX} ${middleY}`;
  }, "");
  const lastChartPoint = chartPoints.at(-1);
  const areaPath = chartPoints.length ? `${linePath} L ${WIDTH} ${HEIGHT} L 0 ${HEIGHT} Z` : "";

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.75rem" }}>
        <div>
        <div className="card-title">LIVE ENERGY LOAD</div>
        <h2 className="energy-chart-title">Power trend <span>kW</span></h2>
        <div style={{ marginTop: "0.25rem" }}>
          <span className="badge warning">
            {demoMode
                ? "DEMO SIMULATION · NOT METER DATA"
                : "SIMULATED TELEMETRY · NOT METER DATA"}
            </span>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.75rem", textAlign: "center" }}>
          <div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)", letterSpacing: "0.05em" }}>CURRENT</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--accent)" }}>{currentPower?.toFixed(1) ?? "—"}</div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>kW</div>
          </div>
          <div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)", letterSpacing: "0.05em" }}>AVERAGE</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>{values.length ? avgPower.toFixed(1) : "—"}</div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>kW</div>
          </div>
          <div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)", letterSpacing: "0.05em" }}>PEAK</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--warning-text, #f0a050)" }}>{values.length ? peakPower.toFixed(1) : "—"}</div>
            <div style={{ fontSize: "0.65rem", color: "var(--text-muted)" }}>kW</div>
          </div>
        </div>
      </div>

      <div className="chart-controls" role="group" aria-label="Energy chart time window">
        <span>Recent samples</span>{([30, 60, 120] as const).map(size => <button key={size} type="button" aria-pressed={windowSize === size} onClick={() => setWindowSize(size)}>{size}</button>)}
      </div>
      {/* SVG chart; point titles expose timestamp, power, energy and occupancy on hover. */}
      <div style={{ background: "var(--surface-elevated)", borderRadius: 8, padding: "0.5rem", overflow: "hidden" }}>
        {points.length < 2 ? (
          <div className="energy-empty" role="status">
            <span aria-hidden="true">⌁</span><strong>No energy samples yet</strong>
            <small>Start monitoring or run the demo simulation to see the simulated power stream.</small>
          </div>
        ) : (
          <svg className="energy-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none" role="img" aria-label={`Power trend across ${points.length} recent samples`}>
            <defs><linearGradient id="energy-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--accent-blue)" stopOpacity=".34"/><stop offset="100%" stopColor="var(--accent-blue)" stopOpacity="0"/></linearGradient></defs>
            {/* Grid lines */}
            {[0, 0.25, 0.5, 0.75, 1].map(t => (
              <line key={t} x1={0} y1={HEIGHT * t} x2={WIDTH} y2={HEIGHT * t}
                stroke="var(--border)" strokeWidth="0.3" />
            ))}
            {/* Area fill */}
            <path d={areaPath} fill="url(#energy-fill)" stroke="none" />
            {/* Line */}
            <path d={`${linePath} L ${WIDTH} ${lastChartPoint?.y ?? HEIGHT}`} fill="none" stroke="var(--accent-blue)" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            {points.map((point, index) => {
              const x = chartPoints[index].x;
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
        Showing {points.length} recent samples · metrics use this window · {demoMode ? "DEMO SIMULATION" : "SIMULATED STREAM"}
      </div>
    </div>
  );
}

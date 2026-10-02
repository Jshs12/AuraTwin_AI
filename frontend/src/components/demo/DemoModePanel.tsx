import { useCallback, useEffect, useState } from "react";
import type { DemoBuildingSummary, DemoStatus, SystemEvent } from "../../types/api";
import { api } from "../../services/api";
import { formatISTTimestamp } from "../../utils/time";

interface Props { onSummary?: (summary: DemoBuildingSummary) => void }

function elapsed(seconds: number) {
  const total = Math.floor(seconds);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export function DemoModePanel({ onSummary }: Props) {
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [summary, setSummary] = useState<DemoBuildingSummary | null>(null);
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [busy, setBusy] = useState(false);
  const [activities, setActivities] = useState<SystemEvent[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextSummary, nextActivities] = await Promise.all([
        api.getDemoStatus(), api.getDemoBuildingSummary(), api.getDemoActivity(),
      ]);
      setStatus(nextStatus); setSummary(nextSummary);
      setActivities(nextActivities);
      setRefreshError("");
      onSummary?.(nextSummary);
    } catch {
      setRefreshError("Demo status is unavailable. Check that AuraTwin's API is running.");
    }
  }, [onSummary]);

  useEffect(() => {
    void refresh();
    const poll = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(poll);
  }, [refresh]);

  const action = async (name: "start" | "pause" | "resume" | "stop" | "reset") => {
    setBusy(true); setError("");
    try { setStatus(await api.demoAction(name)); await refresh(); }
    catch (err) { setError(err instanceof Error ? err.message : "Demo action failed"); await refresh(); }
    finally { setBusy(false); }
  };

  const changeSpeed = async (speed: number) => {
    try { setStatus(await api.setDemoSpeed(speed)); }
    catch (err) { setError(err instanceof Error ? err.message : "Speed update failed"); }
  };

  const running = status?.status === "RUNNING";
  const paused = status?.status === "PAUSED";
  const controlEvents = activities.slice(-4).reverse();
  const hasSamples = Boolean(summary?.energy_history.length);
  const metrics = [
    ["Total occupants", hasSamples && summary ? summary.total_occupants : "—"],
    ["Occupied zones", hasSamples && summary ? `${summary.occupied_zones} / ${summary.active_zones}` : "—"],
    ["Average temperature", summary?.average_zone_temperature == null ? "—" : `${summary.average_zone_temperature.toFixed(1)}°C`],
    ["Average setpoint", summary?.average_setpoint == null ? "—" : `${summary.average_setpoint.toFixed(1)}°C`],
    ["SIMULATED POWER", hasSamples && summary ? `${summary.simulated_power_kw.toFixed(1)} kW` : "—"],
    ["SIMULATED ENERGY", hasSamples && summary ? `${summary.simulated_energy_kwh.toFixed(2)} kWh` : "—"],
    ["SIMULATED COST", hasSamples && summary ? `${summary.simulated_cost.toFixed(2)} ${summary.currency}` : "—"],
    ["Active controls", hasSamples && summary ? summary.zones_under_active_control : "—"],
  ] as const;

  return (
    <section className="card demo-mode-panel" aria-label="AuraTwin Demo Mode">
      <div className="demo-banner">⚠ DEMO SIMULATION — NOT CONNECTED TO A PHYSICAL BUILDING</div>
      <div className="demo-heading-row">
        <div>
          <div className="card-title">AURATWIN DEMO MODE</div>
          <strong>{status?.scenario_name ?? "Building Occupancy Response"}</strong>
        </div>
        <span className={`badge ${running ? "success" : "warning"}`}>{status?.status ?? "IDLE"}</span>
      </div>
      <div className="demo-controls">
        {!running && !paused && <button className="btn btn-primary" disabled={busy} onClick={() => void action("start")}>▶ START</button>}
        {running && <button className="btn btn-secondary" disabled={busy} onClick={() => void action("pause")}>Ⅱ PAUSE</button>}
        {paused && <button className="btn btn-primary" disabled={busy} onClick={() => void action("resume")}>▶ RESUME</button>}
        {(running || paused) && <button className="btn btn-secondary" disabled={busy} onClick={() => void action("stop")}>■ STOP</button>}
        <button className="btn btn-secondary" disabled={busy} onClick={() => void action("reset")}>↺ RESET</button>
        <label className="demo-speed">Speed
          <select value={status?.speed_multiplier ?? 1} onChange={e => void changeSpeed(Number(e.target.value))}>
            {[0.5, 1, 2, 5].map(speed => <option key={speed} value={speed}>{speed}x</option>)}
          </select>
        </label>
      </div>
      <div className="demo-phase-row">
        <span>Phase: <b>{status?.current_phase ?? "NOT STARTED"}</b></span>
        <span>{status?.phase_number ?? 0} / {status?.total_phases ?? 4}</span>
        <span>Elapsed: {elapsed(status?.elapsed_seconds ?? 0)}</span>
        <span>Provider: {summary?.provider ?? "SIMULATED BACNET"}</span>
        <span>Safety: {status?.safety_profile ?? "normal policy · demo inactive"}</span>
      </div>
      {refreshError && <div className="demo-error" role="alert">{refreshError}</div>}
      {error && <div className="demo-error" role="alert">{error}</div>}
      <div className="demo-metrics">
        {metrics.map(([label, value]) => <div className="demo-metric" key={label}>
          <small>{label}</small><strong>{value}</strong>
          <small>SIMULATED</small>
        </div>)}
      </div>
      <div className="demo-activity">
        <div className="card-title">LATEST CONTROL COMMANDS · SIMULATED BACNET</div>
        <div className="muted">Occupancy shown is the value at command time. Timestamps distinguish commands from current zone state.</div>
        {controlEvents.length === 0 ? <span className="muted">Start demo to see validated control activity.</span> : controlEvents.map(event => {
          const p = event.payload;
          return <div className="demo-activity-row" key={event.event_id}>
            <b>{event.zone_id.replace(/_/g, " ")}</b><span>{String(p.occupancy_at_command ?? "—")} occupants at command</span>
            <span>requested {String(p.requested_setpoint ?? "—")}°C · applied {String(p.applied_setpoint ?? "—")}°C</span>
            <span>{formatISTTimestamp(String(p.timestamp ?? event.timestamp))}</span>
            <b style={{ color: p.success ? "var(--success)" : "var(--warning)" }}>{String(p.status ?? (p.success ? "SUCCESS" : "FAILED"))}</b>
            <small className="muted" style={{ gridColumn: "1 / -1" }}>Command ID: {String(p.command_id ?? event.event_id)}</small>
          </div>;
        })}
      </div>
    </section>
  );
}

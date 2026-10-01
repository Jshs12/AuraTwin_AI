import type { DemoBuildingSummary } from "../../types/api";
import type { EnergyDataPoint } from "../../hooks/useMonitoring";
import type { Zone, MonitoringStatus, SystemEvent } from "../../types/api";
import { formatISTTimestamp } from "../../utils/time";

export function BuildingCommandCenter({ buildingName, zones, status, events, power, energyHistory, demoSummary }:{
  buildingName: string; zones: Zone[]; status: MonitoringStatus | null; events: SystemEvent[];
  power: number; energyHistory: EnergyDataPoint[]; demoSummary?: DemoBuildingSummary | null;
}) {
  const latest = events[0];
  const monitored = new Map((status?.zones ?? []).map(zone => [zone.zone_id, zone]));
  const occupancy = zones.map(zone => ({ zone, count: monitored.get(zone.zone_id)?.last_people_count ?? zone.current_occupancy ?? 0 }));
  const total = occupancy.reduce((sum, item) => sum + item.count, 0);
  const updatedAt = latest?.timestamp ?? energyHistory.at(-1)?.timestamp;
  return <section className="command-center" aria-label="Building command center">
    <div className="command-center-heading">
      <div><p className="eyebrow">AURATWIN AI · BUILDING INTELLIGENCE</p><h1>{buildingName || "Building overview"}</h1>
        <p>Occupancy, comfort and energy signals for this authorized building.</p></div>
      <div className="command-center-status">
        <span className={`live-pill ${status?.running ? "is-live" : ""}`}><i />{status?.running ? "Monitoring active" : "Monitoring stopped"}</span>
        <span className="badge warning">{demoSummary?.scenario_id || status?.demo_simulation ? "DEMO SIMULATION" : "SIMULATED SYSTEMS"}</span>
        {updatedAt && <small>Updated {formatISTTimestamp(updatedAt)}</small>}
      </div>
    </div>
    <div className="command-center-grid">
      <article className="command-panel occupancy-panel">
        <div className="panel-heading"><div><span className="panel-kicker">OCCUPANCY</span><h2>{total} <small>people</small></h2></div><span className="badge warning">SIMULATED / PROVIDER REPORTED</span></div>
        <div className="occupancy-bars">{occupancy.length ? occupancy.slice(0, 6).map(({ zone, count }) => <div className="occupancy-bar-row" key={zone.zone_id}>
          <span title={zone.name}>{zone.name}</span><div className="bar-track"><i style={{ width: `${Math.min(100, zone.capacity > 0 ? count / zone.capacity * 100 : 0)}%` }} /></div><b>{count}<small> / {zone.capacity}</small></b>
        </div>) : <div className="product-empty-state">No active zones are configured.</div>}</div>
        {occupancy.length > 6 && <small className="muted">Showing 6 of {occupancy.length} configured zones.</small>}
      </article>
      <article className="command-panel power-panel"><div className="panel-heading"><div><span className="panel-kicker">CURRENT POWER</span><h2>{power.toFixed(1)} <small>kW</small></h2></div><span className="badge warning">NOT METER DATA</span></div>
        <div className="command-sparkline">{energyHistory.length > 1 ? <svg viewBox="0 0 100 32" preserveAspectRatio="none" role="img" aria-label="Recent simulated power trend">
          <polyline points={energyHistory.slice(-48).map((point, i, list) => `${i / Math.max(list.length - 1, 1) * 100},${30 - Math.max(0, Math.min(1, point.power_kw / Math.max(...list.map(p => p.power_kw), 1))) * 26}`).join(" ")} />
        </svg> : <p>Waiting for energy samples</p>}</div>
        <small>Recent illustrative energy stream · simulated only</small>
      </article>
      <article className="command-panel flow-panel"><span className="panel-kicker">AURATWIN WORKFLOW</span><div className="workflow-steps">
        <div><b>01</b><span>Occupancy & environment</span><small>{zones.length} configured zones</small></div><i aria-hidden="true">›</i>
        <div><b>02</b><span>Recommendation</span><small>Advisory · safety checked</small></div><i aria-hidden="true">›</i>
        <div><b>03</b><span>HVAC response</span><small>Simulated provider</small></div><i aria-hidden="true">›</i>
        <div><b>04</b><span>Energy feedback</span><small>{energyHistory.length ? `${energyHistory.length} samples` : "Awaiting samples"}</small></div>
      </div><p>Control remains behind data quality, freshness, safety policy and operator control state.</p></article>
      <article className="command-panel activity-panel"><span className="panel-kicker">LATEST SYSTEM ACTIVITY</span>
        {latest ? <><h3>{latest.event_type.replaceAll("_", " ")}</h3><p>{latest.zone_id.replaceAll("_", " ")} · {formatISTTimestamp(latest.timestamp)}</p><small>{latest.source}{(latest.payload as Record<string, unknown>).simulated === true ? " · SIMULATED" : ""}</small></>
          : <div className="product-empty-state compact">No events recorded yet.</div>}
      </article>
    </div>
  </section>;
}

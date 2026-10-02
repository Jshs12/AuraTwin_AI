import type { DemoBuildingSummary } from "../../types/api";
import type { EnergyDataPoint } from "../../hooks/useMonitoring";
import type { Zone, MonitoringStatus, SystemEvent } from "../../types/api";
import { formatISTTimestamp } from "../../utils/time";
import type { OptimizationInterval } from "../../services/api";

export function BuildingCommandCenter({ buildingName, zones, status, events, power, energyHistory, demoSummary, activeIntervals, latestCompleted }:{
  buildingName: string; zones: Zone[]; status: MonitoringStatus | null; events: SystemEvent[];
  power: number | null; energyHistory: EnergyDataPoint[]; demoSummary?: DemoBuildingSummary | null;
  activeIntervals: OptimizationInterval[]; latestCompleted?: OptimizationInterval | null;
}) {
  const latest = events[0];
  const monitored = new Map((status?.zones ?? []).map(zone => [zone.zone_id, zone]));
  const occupancy = zones.map(zone => {
    const zoneStatus = monitored.get(zone.zone_id);
    const observedPeople = zoneStatus?.last_snapshot ? zoneStatus.last_people_count : zone.current_occupancy;
    return { zone, count: typeof observedPeople === "number" ? observedPeople : null };
  });
  const knownOccupancy = occupancy.filter(item => item.count !== null);
  const total = knownOccupancy.length ? knownOccupancy.reduce((sum, item) => sum + item.count!, 0) : null;
  const capacity = zones.reduce((sum, zone) => sum + zone.capacity, 0);
  const temperatures = zones.map(zone => zone.current_temperature).filter((value): value is number => typeof value === "number");
  const setpoints = zones.map(zone => zone.current_setpoint).filter((value): value is number => typeof value === "number");
  const averageTemperature = demoSummary?.average_zone_temperature ?? (temperatures.length ? temperatures.reduce((a, b) => a + b, 0) / temperatures.length : null);
  const averageSetpoint = demoSummary?.average_setpoint ?? (setpoints.length ? setpoints.reduce((a, b) => a + b, 0) / setpoints.length : null);
  const buildingProvenance = demoSummary?.scenario_id || status?.demo_simulation ? "DEMO SIMULATION" : "SIMULATED SYSTEMS";
  const updatedAt = latest?.timestamp ?? energyHistory.at(-1)?.timestamp;
  return <section className="command-center" aria-label="Building command center">
    <div className="command-center-heading">
      <div><p className="eyebrow">AURATWIN AI · BUILDING INTELLIGENCE</p><h1>{buildingName || "Building overview"}</h1>
        <p>Building energy intelligence · occupancy, comfort, safe control and measured impact.</p></div>
      <div className="command-center-status">
        <span className={`live-pill ${status?.running ? "is-live" : ""}`}><i />{status?.running ? "Monitoring active" : "Monitoring stopped"}</span>
        <span className="badge warning">{buildingProvenance}</span>
        {updatedAt && <small>Updated {formatISTTimestamp(updatedAt)}</small>}
      </div>
    </div>
    <div className="command-metrics" aria-label="Building at a glance">
      <article className="metric-occupancy"><small>TOTAL OCCUPANCY</small><strong>{total ?? "—"}<span>{total === null ? " no current sample" : ` / ${capacity} people`}</span></strong><em>{knownOccupancy.length} of {zones.length} zones with a snapshot</em></article>
      <article><small>SIMULATED ENERGY LOAD</small><strong>{power === null ? "—" : power.toFixed(1)}<span>{power === null ? " no samples" : " kW"}</span></strong><em>SIMULATED · NOT METER DATA</em></article>
      <article><small>AVERAGE TEMPERATURE</small><strong>{averageTemperature === null ? "—" : `${averageTemperature.toFixed(1)}°`}<span>{averageTemperature === null ? " no validated value" : "C"}</span></strong><em>Zone snapshot</em></article>
      <article><small>AVERAGE HVAC SETPOINT</small><strong>{averageSetpoint === null ? "—" : `${averageSetpoint.toFixed(1)}°`}<span>{averageSetpoint === null ? " no current value" : "C"}</span></strong><em>Simulated HVAC</em></article>
      <article><small>ACTIVE OPTIMIZATIONS</small><strong>{activeIntervals.length}<span> holding</span></strong><em>Safety gates remain active</em></article>
    </div>
    <div className="command-center-grid">
      <article className="command-panel occupancy-panel">
        <div className="panel-heading"><div><span className="panel-kicker">BUILDING OCCUPANCY</span><h2>{total ?? "—"} <small>people</small></h2></div><span className="badge warning">{buildingProvenance}</span></div>
        <div className="occupancy-bars">{occupancy.length ? occupancy.slice(0, 6).map(({ zone, count }) => <div className="occupancy-bar-row" key={zone.zone_id}>
          <span title={zone.name}>{zone.name}</span><div className="bar-track"><i style={{ width: `${Math.min(100, zone.capacity > 0 && count !== null ? count / zone.capacity * 100 : 0)}%` }} /></div><b>{count ?? "—"}<small> / {zone.capacity}</small></b>
        </div>) : <div className="product-empty-state">No active zones are configured.</div>}</div>
        {occupancy.length > 6 && <small className="muted">Showing 6 of {occupancy.length} configured zones.</small>}
      </article>
      <article className="command-panel power-panel"><div className="panel-heading"><div><span className="panel-kicker">CURRENT POWER</span><h2>{power === null ? "—" : power.toFixed(1)} <small>{power === null ? "awaiting samples" : "kW"}</small></h2></div><span className="badge warning">SIMULATED · NOT METER DATA</span></div>
        <div className="command-sparkline">{energyHistory.length > 1 ? <svg viewBox="0 0 100 32" preserveAspectRatio="none" role="img" aria-label="Recent simulated power trend">
          <polyline points={energyHistory.slice(-48).map((point, i, list) => `${i / Math.max(list.length - 1, 1) * 100},${30 - Math.max(0, Math.min(1, point.power_kw / Math.max(...list.map(p => p.power_kw), 1))) * 26}`).join(" ")} />
        </svg> : <p>Waiting for energy samples</p>}</div>
        <small>Recent illustrative energy stream · simulated only</small>
      </article>
      <article className="command-panel flow-panel"><div className="panel-heading"><div><span className="panel-kicker">OCCUPANCY → HVAC → IMPACT</span><h2>Optimization story</h2></div><span className="badge neutral">NO SAVINGS CLAIM</span></div>
        {latestCompleted ? <div className="optimization-story" aria-label="Latest completed optimization interval">
          <div><small>OCCUPANCY</small><strong>{latestCompleted.starting_occupancy} → {latestCompleted.ending_occupancy ?? "—"} people</strong></div>
          <i aria-hidden="true">›</i><div><small>SETPOINT</small><strong>{latestCompleted.previous_setpoint.toFixed(1)}° → {latestCompleted.optimized_setpoint.toFixed(1)}°C</strong></div>
          <i aria-hidden="true">›</i><div><small>INTERVAL ENERGY</small><strong>{latestCompleted.energy_consumed_kwh === null ? "Unavailable" : `${latestCompleted.energy_consumed_kwh.toFixed(3)} kWh`}</strong></div>
          <i aria-hidden="true">›</i><div><small>COST</small><strong>{latestCompleted.cost_consumed === null ? "Unavailable" : `${latestCompleted.currency ?? ""} ${latestCompleted.cost_consumed.toFixed(3)}`}</strong></div>
          <p className="story-footnote">Savings not yet measurable · no validated comparison baseline{latestCompleted.simulated ? " · SIMULATED TELEMETRY" : ""}</p>
        </div> : activeIntervals.length ? <div className="optimization-story active-story">
          <div><small>OCCUPANCY</small><strong>{activeIntervals[0].starting_occupancy} people</strong></div><i aria-hidden="true">›</i>
          <div><small>SETPOINT</small><strong>{activeIntervals[0].previous_setpoint.toFixed(1)}° → {activeIntervals[0].optimized_setpoint.toFixed(1)}°C</strong></div><i aria-hidden="true">›</i>
          <div><small>STATUS</small><strong>Holding until occupancy changes</strong></div>
          <p className="story-footnote">Energy and cost are calculated only after the interval closes and validated telemetry boundaries are available.</p>
        </div> : <div className="optimization-story empty-story"><strong>No completed optimization interval yet</strong><span>Start monitoring to follow occupancy through recommendation, safety validation, simulated control, and telemetry feedback.</span></div>}
        <p>Every control decision remains subject to data quality, freshness, safety policy and operator control state.</p></article>
      <article className="command-panel activity-panel"><span className="panel-kicker">LATEST SYSTEM ACTIVITY</span>
        {latest ? <><h3>{latest.event_type.replaceAll("_", " ")}</h3><p>{latest.zone_id.replaceAll("_", " ")} · {formatISTTimestamp(latest.timestamp)}</p><small>{latest.source}{(latest.payload as Record<string, unknown>).simulated === true ? " · SIMULATED" : ""}</small></>
          : <div className="product-empty-state compact">No events recorded yet.</div>}
      </article>
    </div>
    {!!activeIntervals.length && <section className="command-panel overview-intervals"><div className="panel-heading"><div><span className="panel-kicker">ACTIVE OPTIMIZATIONS</span><h2>{activeIntervals.length} <small>holding</small></h2></div><span className="badge warning">SIMULATED HVAC · SAFETY GATES ACTIVE</span></div>
      <div className="overview-interval-list">{activeIntervals.map(interval => <article key={interval.interval_id}>
        <strong>{zones.find(zone => zone.zone_id === interval.zone_id)?.name ?? interval.zone_id}</strong>
        <span>{interval.starting_occupancy} people · {interval.starting_temperature.toFixed(1)}°C</span>
        <b>{interval.previous_setpoint.toFixed(1)}°C → {interval.optimized_setpoint.toFixed(1)}°C</b>
        <small>Holding until occupancy changes · started {formatISTTimestamp(interval.started_at)}</small>
      </article>)}</div>
    </section>}
  </section>;
}

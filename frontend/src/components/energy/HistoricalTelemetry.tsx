import { useEffect, useMemo, useState } from "react";
import { api } from "../../services/api";
import type { Floor, HistoricalTelemetryPoint, TelemetryAnalyticsResponse, TelemetrySignal, Zone } from "../../types/api";

const signals: Array<{ value: TelemetrySignal; label: string }> = [
  { value: "occupancy", label: "Occupancy" }, { value: "temperature", label: "Temperature" },
  { value: "power", label: "Power" }, { value: "energy", label: "Cumulative energy" },
  { value: "cost", label: "Estimated cumulative cost" }, { value: "tariff_rate", label: "Tariff rate" },
];

function timeLabel(timestamp: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    .format(new Date(timestamp));
}

export function HistoricalTelemetry({ buildingId, zones }: { buildingId?: string; zones: Zone[] }) {
  const [floors, setFloors] = useState<Floor[]>([]);
  const [floorId, setFloorId] = useState("");
  const [zoneId, setZoneId] = useState("");
  const [signal, setSignal] = useState<TelemetrySignal>("power");
  const [rangeHours, setRangeHours] = useState(24);
  const [result, setResult] = useState<TelemetryAnalyticsResponse | null>(null);
  const [loadingFloors, setLoadingFloors] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setResult(null); setError(""); setFloorId(""); setZoneId(""); setFloors([]);
    if (!buildingId) return;
    setLoadingFloors(true);
    api.getFloors(buildingId).then(items => {
      if (!active) return;
      setFloors(items);
      setFloorId(items[0]?.floor_id ?? "");
    }).catch(err => { if (active) setError(err instanceof Error ? err.message : "Unable to load floors"); })
      .finally(() => { if (active) setLoadingFloors(false); });
    return () => { active = false; };
  }, [buildingId]);

  const visibleZones = useMemo(() => zones.filter(zone => !floorId || zone.floor_id === floorId), [zones, floorId]);
  useEffect(() => {
    if (zoneId && !visibleZones.some(zone => zone.zone_id === zoneId)) setZoneId("");
  }, [visibleZones, zoneId]);

  useEffect(() => {
    let active = true;
    if (!buildingId || loadingFloors || !floorId) { setResult(null); return; }
    const endTime = new Date();
    const startTime = new Date(endTime.getTime() - rangeHours * 60 * 60 * 1000);
    setLoading(true); setError("");
    api.getHistoricalTelemetry({ buildingId, floorId, zoneId: zoneId || undefined,
      signal, startTime: startTime.toISOString(), endTime: endTime.toISOString() })
      .then(data => { if (active) setResult(data); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "Unable to load historical telemetry"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [buildingId, floorId, zoneId, signal, rangeHours, loadingFloors]);

  const points = useMemo(() => (result?.observations ?? [])
    .filter(point => point.signal === signal)
    .slice().sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at)), [result, signal]);
  const aggregate = result?.aggregations.find(item => item.signal === signal);
  const simulatedCount = points.filter(point => point.simulated === true).length;
  const unknownProvenanceCount = points.filter(point => point.simulated == null).length;
  const values = points.map(point => point.value);
  const min = aggregate?.minimum ?? (values.length ? Math.min(...values) : null);
  const max = aggregate?.maximum ?? (values.length ? Math.max(...values) : null);
  const avg = aggregate?.average ?? null;
  const unit = points[0]?.unit ?? "";
  const width = 1000, height = 220, pad = 24;
  const chartMin = values.length ? Math.min(...values) : 0;
  const chartMax = values.length ? Math.max(...values) : 1;
  const yMin = chartMin, yMax = chartMax;
  const yRange = Math.max(yMax - yMin, Math.abs(yMax) * 0.02, 0.01);
  const firstTime = points.length ? Date.parse(points[0].observed_at) : 0;
  const lastTime = points.length ? Date.parse(points[points.length - 1].observed_at) : 1;
  const coords = points.map(point => ({ point,
    x: pad + ((Date.parse(point.observed_at) - firstTime) / Math.max(lastTime - firstTime, 1)) * (width - 2 * pad),
    y: height - pad - ((point.value - yMin) / yRange) * (height - 2 * pad),
  }));

  return <section className="card historical-telemetry" aria-label="Historical telemetry">
    <header className="historical-header">
      <div><div className="card-title">HISTORICAL TELEMETRY</div>
        <p className="muted">Persisted observations by observation time. Live runtime monitoring is shown separately above.</p></div>
      <span className="badge">REST HISTORY</span>
    </header>
    <div className="historical-filters">
      <label>Floor<select value={floorId} onChange={event => { setFloorId(event.target.value); setZoneId(""); }} disabled={!floors.length}>
        {floors.map(floor => <option key={floor.floor_id} value={floor.floor_id}>{floor.name}</option>)}
      </select></label>
      <label>Zone<select value={zoneId} onChange={event => setZoneId(event.target.value)}>
        <option value="">All zones on floor</option>
        {visibleZones.map(zone => <option key={zone.zone_id} value={zone.zone_id}>{zone.name}</option>)}
      </select></label>
      <label>Signal<select value={signal} onChange={event => setSignal(event.target.value as TelemetrySignal)}>
        {signals.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
      </select></label>
      <label>Time range<select value={rangeHours} onChange={event => setRangeHours(Number(event.target.value))}>
        <option value={24}>Last 24 hours</option><option value={168}>Last 7 days</option><option value={720}>Last 30 days</option>
      </select></label>
    </div>
    <div className="historical-provenance">
      <span className="badge warning">Historical data · not live</span>
      {simulatedCount > 0 && <span className="badge warning">{simulatedCount} simulated point{simulatedCount === 1 ? "" : "s"}</span>}
      {unknownProvenanceCount > 0 && <span className="badge">{unknownProvenanceCount} point{unknownProvenanceCount === 1 ? "" : "s"} with simulation status unavailable</span>}
    </div>
    <p className="muted">A provider's non-simulated flag does not prove sensor accuracy. Occupancy history is count metadata only; no camera content is available here.</p>
    {loadingFloors && <p className="muted" role="status">Loading floors…</p>}
    {!loadingFloors && !floorId && <p className="historical-empty">No active floors are configured for this building.</p>}
    {!buildingId && <p className="historical-empty">Select an authorized building to view history.</p>}
    {loading && <p className="muted" role="status">Loading persisted history…</p>}
    {error && <p className="demo-error" role="alert">{error}</p>}
    {!loading && !error && points.length === 0 && <p className="historical-empty">No persisted {signal.replace("_", " ")} observations in this time range.</p>}
    {points.length > 0 && <>
      <div className="historical-metrics">
        <div><small>LATEST</small><strong>{points.at(-1)!.value.toFixed(2)} {unit}</strong></div>
        <div><small>MINIMUM</small><strong>{min!.toFixed(2)} {unit}</strong></div>
        <div><small>MAXIMUM</small><strong>{max!.toFixed(2)} {unit}</strong></div>
        <div><small>AVERAGE</small><strong>{avg == null ? "—" : `${avg.toFixed(2)} ${unit}`}</strong></div>
        <div><small>POINTS</small><strong>{aggregate?.count ?? points.length}</strong></div>
      </div>
      <div className="historical-chart" role="img" aria-label={`${signals.find(item => item.value === signal)?.label} history, ${points.length} observation points`}>
        <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
          {[0, .25, .5, .75, 1].map(t => <line key={t} x1={pad} x2={width - pad} y1={pad + t * (height - 2 * pad)} y2={pad + t * (height - 2 * pad)} />)}
          {coords.map(({ point, x, y }) => <circle key={`${point.observed_at}-${point.zone_id}`} cx={x} cy={y} r="4">
            <title>{`${timeLabel(point.observed_at)} · ${point.value} ${point.unit} · ${point.simulated === true ? "simulated" : point.simulated === false ? "provider marks non-simulated" : "simulation status unavailable"} · source ${point.source ?? "unavailable"} · quality ${point.quality_state ?? "unavailable"}`}</title>
          </circle>)}
        </svg>
      </div>
      <div className="historical-axis"><span>{timeLabel(points[0].observed_at)}</span><span>Observation time</span><span>{timeLabel(points.at(-1)!.observed_at)}</span></div>
      {result?.truncated && <p className="muted">Showing the newest records up to the configured query limit.</p>}
      <div className="historical-table-wrap"><table className="historical-table"><thead><tr><th>Observed</th><th>Zone</th><th>Value</th><th>Source</th><th>Quality</th><th>Simulation</th></tr></thead>
        <tbody>{points.slice(-8).reverse().map((point: HistoricalTelemetryPoint) => <tr key={`${point.zone_id}-${point.observed_at}`}>
          <td>{timeLabel(point.observed_at)}</td><td>{zones.find(zone => zone.zone_id === point.zone_id || zone.database_zone_id === point.zone_id)?.name ?? point.zone_id}</td>
          <td>{point.value} {point.unit}</td><td>{point.source ?? "Unavailable"}</td><td>{point.quality_state ?? "Unavailable"}</td>
          <td>{point.simulated === true ? "Simulated" : point.simulated === false ? "Provider marks non-simulated" : "Unavailable"}</td>
        </tr>)}</tbody></table></div>
    </>}
  </section>;
}

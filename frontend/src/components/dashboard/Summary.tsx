import type { Zone, MonitoringStatus } from "../../types/api";
import type { DemoBuildingSummary } from "../../types/api";

interface SummaryProps {
  zones: Zone[];
  totalOccupancy: number;
  occupiedZones: number;
  monitoringStatus: MonitoringStatus | null;
  currentPower: number;
  demoSummary?: DemoBuildingSummary | null;
}

export function Summary({ zones, totalOccupancy, occupiedZones, monitoringStatus, currentPower, demoSummary }: SummaryProps) {
  const monitoredZones = monitoringStatus?.zones_enabled ?? 0;
  const totalZones = monitoringStatus?.zones_total ?? zones.length;
  const temperatures = zones.map(zone => zone.current_temperature).filter((value): value is number => typeof value === "number");
  const setpoints = zones.map(zone => zone.current_setpoint).filter((value): value is number => typeof value === "number");
  const averageTemperature = demoSummary?.average_zone_temperature ?? (temperatures.length ? temperatures.reduce((a, b) => a + b, 0) / temperatures.length : null);
  const averageSetpoint = demoSummary?.average_setpoint ?? (setpoints.length ? setpoints.reduce((a, b) => a + b, 0) / setpoints.length : null);

  return (
    <div className="summary-grid">
      <div className="card">
        <div className="card-title">Total Occupancy</div>
        <div>
          <span className="metric-value">{totalOccupancy}</span>
          <span className="metric-unit"> people</span>
        </div>
        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
          Across {occupiedZones} occupied monitored zone{occupiedZones !== 1 ? "s" : ""}
        </div>
      </div>

      <div className="card">
        <div className="card-title">{monitoringStatus?.demo_simulation ? "DEMO SIMULATION SCOPE" : "CONFIGURED BUILDING MONITORING"}</div>
        <div><span className="metric-value">{monitoringStatus?.demo_simulation ? (monitoringStatus.demo_zones_total ?? monitoredZones) : monitoredZones}</span>
          <span className="metric-unit"> / {monitoringStatus?.demo_simulation ? (monitoringStatus.configured_zones_total ?? zones.length) : totalZones} {monitoringStatus?.demo_simulation ? "demo zones" : "active zones"}</span></div>
        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
          {monitoringStatus?.demo_simulation
            ? `Demo scenario selection; configured building has ${monitoringStatus.configured_zones_total ?? zones.length} active zones.`
            : `Configured scope · occupied monitored: ${occupiedZones} / ${monitoredZones}`}
        </div>
      </div>

      <div className="card">
        <div className="card-title">Occupancy Provider</div>
        <div style={{ marginTop: "0.25rem" }}>
          <span className={`badge ${monitoringStatus?.demo_simulation ? "warning" : "success"}`}>
            {monitoringStatus?.demo_simulation
              ? "DEMO INPUT · SIMULATED"
              : monitoringStatus?.occupancy_provider === "yolo"
                ? monitoringStatus.occupancy_provider_ready ? "YOLO PERSON DETECTION" : "YOLO · NOT READY"
                : "MOCK OCCUPANCY · SIMULATED"}
          </span>
        </div>
        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.375rem" }}>
          {monitoringStatus?.demo_simulation
            ? "Deterministic scenario input; no camera measurement"
            : monitoringStatus?.occupancy_provider === "yolo"
              ? monitoringStatus.occupancy_provider_ready ? "Image-based person inference provider" : "YOLO is configured but its model is unavailable"
              : "Deterministic mock occupancy; not image-based inference"}
        </div>
      </div>

      <div className="card">
        <div className="card-title">Runtime Power</div>
        <div>
          <span className="metric-value">{currentPower.toFixed(1)}</span>
          <span className="metric-unit"> kW</span>
        </div>
        <div style={{ marginTop: "0.25rem" }}>
          <span className="badge warning">SIMULATED · NOT METER DATA</span>
        </div>
      </div>
      <div className="card"><div className="card-title">Average Zone Temperature</div>
        <div className="metric-value">{averageTemperature == null ? "—" : `${averageTemperature.toFixed(1)}°C`}</div>
        <small className="muted">{demoSummary?.scenario_id ? "DEMO SIMULATION" : "Configured ZoneState snapshot"}</small>
      </div>
      <div className="card"><div className="card-title">Average Setpoint</div>
        <div className="metric-value">{averageSetpoint == null ? "—" : `${averageSetpoint.toFixed(1)}°C`}</div>
        <small className="muted">{demoSummary?.scenario_id ? "SIMULATED HVAC" : "Configured ZoneState snapshot"}</small>
      </div>
      {demoSummary?.scenario_id && <div className="card"><div className="card-title">Demo Energy & Cost</div>
        <div>{demoSummary.simulated_energy_kwh.toFixed(2)} kWh · {demoSummary.currency} {demoSummary.simulated_cost.toFixed(2)}</div>
        <small className="muted">DEMO SIMULATION · NOT METER DATA · no savings claim</small>
      </div>}
    </div>
  );
}

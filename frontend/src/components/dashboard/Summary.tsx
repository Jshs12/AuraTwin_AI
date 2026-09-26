import type { Zone, MonitoringStatus } from "../../types/api";

interface SummaryProps {
  zones: Zone[];
  totalOccupancy: number;
  occupiedZones: number;
  monitoringStatus: MonitoringStatus | null;
  currentPower: number;
}

export function Summary({ zones, totalOccupancy, occupiedZones, monitoringStatus, currentPower }: SummaryProps) {
  const monitoredZones = monitoringStatus?.zones_enabled ?? 0;
  const totalZones = monitoringStatus?.zones_total ?? zones.length;

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
        <div className="card-title">Monitored Zones</div>
        <div>
          <span className="metric-value">{monitoredZones}</span>
          <span className="metric-unit"> / {totalZones}</span>
        </div>
        <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
          Occupied monitored: {occupiedZones} / {monitoredZones}
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
        <div className="card-title">Current Power</div>
        <div>
          <span className="metric-value">{currentPower.toFixed(1)}</span>
          <span className="metric-unit"> kW</span>
        </div>
        <div style={{ marginTop: "0.25rem" }}>
          <span className="badge warning">SIMULATED</span>
        </div>
      </div>
    </div>
  );
}

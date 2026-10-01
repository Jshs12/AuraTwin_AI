import { useState } from "react";
import { useZoneState } from "../../hooks/useZoneState";
import { Card, OccupancyBadge, Button } from "../common";
import { Timeline } from "../events/Timeline";
import { CVPanel } from "../occupancy/CVPanel";
import { api, ApiRequestError } from "../../services/api";

export function ZoneDetail({ zoneId, demoPhase, canOperate = true }: { zoneId: string | null; demoPhase?: string | null; canOperate?: boolean }) {
  const [modeBusy, setModeBusy] = useState(false);
  const [modeError, setModeError] = useState<ApiRequestError | Error | null>(null);
  const {
    state,
    history,
    recommendation,
    controlResult,
    loading,
    error,
    refresh,
    generateRecommendation,
    applyRecommendation,
  } = useZoneState(zoneId);

  if (!zoneId) {
    return (
      <Card>
        <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "2rem 0" }}>
          ← Select a zone to view details
        </div>
      </Card>
    );
  }

  if (loading && !state) {
    return <Card><div style={{ color: "var(--text-muted)" }}>Loading zone data...</div></Card>;
  }

  if (error || !state) {
    return (
      <Card>
        <div style={{ color: "var(--accent-red)", fontSize: "0.875rem" }}>
          {error || "Unable to connect to AuraTwin backend."}
        </div>
      </Card>
    );
  }

  const { zone, occupancy, temperature, energy, tariff, hvac_status } = state;
  const controlMode = state.control_mode;

  const updateMode = async (action: () => Promise<unknown>) => {
    setModeBusy(true);
    setModeError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setModeError(err instanceof Error ? err : new Error("Unable to update control state"));
    } finally {
      setModeBusy(false);
    }
  };

  return (
    <div className="detail-panel">
      {/* Zone identity */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
          <div>
            <h2 style={{ fontSize: "1.125rem", marginBottom: 0 }}>{zone.name}</h2>
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", textTransform: "uppercase" }}>
              {zone.type} · {zone.area_m2} m²
            </div>
          </div>
          <OccupancyBadge state={occupancy.occupancy_state} />
        </div>

        {/* Occupancy */}
        <div className="section-block">
          <div className="card-title">ACTIVE OCCUPANCY SOURCE</div>
            <div style={{ marginBottom: "0.35rem" }}><span className="badge warning">{state.occupancy_source === "demo_scenario_simulation" ? "DEMO SIMULATION" : state.occupancy_source === "yolo" ? "COMPUTER VISION · YOLO" : `OCCUPANCY PROVIDER · ${(state.occupancy_source ?? "unknown").toUpperCase()}`}</span></div>
          <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginBottom: "0.5rem" }}>Source: {state.occupancy.source ?? state.occupancy_source ?? "unknown"} · {state.occupancy.simulated ? "SIMULATED" : "provider reported"}{state.data_quality?.signals.occupancy ? ` · quality ${state.data_quality.signals.occupancy.state}` : ""}{state.occupancy_source === "demo_scenario_simulation" && demoPhase ? ` · ${demoPhase}` : ""}</div>
          <div className="zone-stats">
            <div>
                <div className="stat-label">{state.occupancy_source === "demo_scenario_simulation" ? "Current occupants" : "Detected people"}</div>
              <div className="stat-value">
                {occupancy.people_count}
                <span style={{ color: "var(--text-muted)", fontWeight: 400 }}> / {zone.capacity}</span>
              </div>
            </div>
            <div>
              <div className="stat-label">Load</div>
              <div className="stat-value">{occupancy.occupancy_percentage.toFixed(1)}%</div>
            </div>
          </div>
        </div>

        {/* Environment */}
        <div className="section-block">
          <div className="card-title">Environment <span className="badge warning" style={{ fontSize: "0.65rem" }}>SIMULATED</span></div>
          <div className="zone-stats">
            <div>
              <div className="stat-label">Current Temp</div>
              <div className="stat-value">{temperature.toFixed(1)}°C</div>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                Source: {state.temperature_source ?? "unknown"} · {state.temperature_simulated ? "SIMULATED" : "provider reported"}
                {state.data_quality?.signals.temperature ? ` · quality ${state.data_quality.signals.temperature.state}` : ""}
              </div>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                Comfort: {zone.comfort.min_temperature}°–{zone.comfort.max_temperature}°
              </div>
            </div>
            <div>
              <div className="stat-label">HVAC Setpoint</div>
              <div className="stat-value">{hvac_status.present_value.toFixed(1)}°C</div>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>{hvac_status.setpoint_source ?? hvac_status.provider} · {(hvac_status.setpoint_simulated ?? hvac_status.simulated) ? "SIMULATED" : "provider reported"}{state.data_quality?.signals.hvac_setpoint ? ` · quality ${state.data_quality.signals.hvac_setpoint.state}` : ""}</div>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>{hvac_status.object_id}</div>
            </div>
          </div>
        </div>

        {/* Building control provenance; explicitly simulated, never a hardware connection claim. */}
        <div className="section-block">
          <div className="card-title">Control provider: SIMULATED BACNET <span className="badge warning">SIMULATED</span></div>
          <div className="zone-stats">
            <div>
              <div className="stat-label">Control State</div>
              <div className="stat-value">{hvac_status.control_state}</div>
            </div>
            <div>
              <div className="stat-label">Last Requested Setpoint</div>
              <div className="stat-value">{hvac_status.requested_setpoint?.toFixed(1) ?? "—"}°C</div>
            </div>
            <div>
              <div className="stat-label">Applied Setpoint</div>
              <div className="stat-value">{hvac_status.present_value.toFixed(1)}°C</div>
            </div>
            <div>
              <div className="stat-label">Previous Setpoint</div>
              <div className="stat-value">{hvac_status.previous_setpoint?.toFixed(1) ?? "—"}°C</div>
            </div>
            <div>
              <div className="stat-label">HVAC Response</div>
              <div className="stat-value">{hvac_status.hvac_mode ?? "UNKNOWN"}</div>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                Fan {hvac_status.fan_status === null ? "unknown" : hvac_status.fan_status ? "on" : "off"}
                {hvac_status.power_kw !== null ? ` · ${hvac_status.power_kw.toFixed(2)} kW` : ""}
              </div>
            </div>
          </div>
          <div className="zone-stats" style={{ marginTop: "0.75rem" }}>
            <div>
              <div className="stat-label">Autonomous control</div>
              <div className="stat-value">{controlMode?.control_enabled ? "ENABLED" : "DISABLED"}</div>
            </div>
            <div>
              <div className="stat-label">Manual override</div>
              <div className="stat-value">{controlMode?.manual_override ? "ACTIVE" : "INACTIVE"}</div>
            </div>
            {controlMode?.fail_safe_active && <div><span className="badge danger">FAIL SAFE LATCHED</span></div>}
          </div>
          {controlMode?.provider_failure_latched && (
            <div className="badge warning" style={{ marginTop: "0.5rem" }}>
              Provider reports {controlMode.provider_recovered ? "recovered" : "failure"}; operator re-enable and fresh validation are required.
            </div>
          )}
          {canOperate && controlMode && (
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
              <Button disabled={modeBusy} variant={controlMode.manual_override ? "primary" : "neutral"}
                onClick={() => updateMode(() => api.setManualOverride(zoneId, !controlMode.manual_override))}>
                {controlMode.manual_override ? "Deactivate Manual Override" : "Enable Manual Override"}
              </Button>
              <Button disabled={modeBusy} variant={controlMode.control_enabled ? "neutral" : "primary"}
                onClick={() => updateMode(() => api.setZoneControlEnabled(zoneId, !controlMode.control_enabled))}>
                {controlMode.control_enabled ? "Disable Autonomous Control" : "Re-enable Autonomous Control"}
              </Button>
            </div>
          )}
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.35rem" }}>
            Manual override pauses AuraTwin commands; it does not send a manual HVAC setpoint.
          </div>
          {modeError && <div className="demo-error" role="alert" style={{ marginTop: "0.5rem" }}>
            {modeError instanceof ApiRequestError && modeError.code === "COMMAND_POLICY_INCOMPLETE" ? <>
              <strong>CONTROL UNAVAILABLE</strong><br />Safety command-limit policy is incomplete.
              {!!modeError.missingConfiguration?.length && <><br />Missing configuration: {modeError.missingConfiguration.join(", ")}</>}
            </> : modeError instanceof ApiRequestError && modeError.code === "COMMAND_POLICY_INVALID" ? <>
              <strong>CONTROL UNAVAILABLE</strong><br />Safety command-limit policy is invalid.
              {!!modeError.invalidConfiguration?.length && <><br />Invalid configuration: {modeError.invalidConfiguration.join(", ")}</>}
            </> : <>{modeError.message}</>}
          </div>}
          {controlResult && (
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.5rem" }}>
              Last command: {controlResult.status} · {controlResult.provider}
              {controlResult.error_message ? ` · ${controlResult.error_message}` : ""}
            </div>
          )}
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.35rem" }}>
            BACnet-ready simulated control; no physical controller is connected.
          </div>
        </div>

        {/* Energy */}
        <div className="section-block">
          <div className="card-title">Energy <span className="badge warning" style={{ fontSize: "0.65rem" }}>SIMULATED</span></div>
          <div className="zone-stats">
            <div>
              <div className="stat-label">Power</div>
              <div className="stat-value">{energy.power_kw.toFixed(2)} kW</div>
            </div>
            <div>
              <div className="stat-label">Energy (accum.)</div>
              <div className="stat-value">{energy.energy_kwh.toFixed(3)} kWh</div>
            </div>
            <div>
              <div className="stat-label">Est. Cost</div>
              <div className="stat-value">{tariff.currency} {energy.cost.toFixed(4)}</div>
            </div>
            <div>
              <div className="stat-label">Tariff</div>
              <div>
                <span className={`badge ${tariff.is_peak ? "danger" : "success"}`}>
                  {tariff.is_peak ? "PEAK" : "OFF-PEAK"}
                </span>
              </div>
              <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
                {tariff.rate_per_kwh} {tariff.currency}/kWh
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* CV Upload Panel */}
      {canOperate && <CVPanel zoneId={zoneId} />}

      {/* Recommendation */}
      <Card title="Recommendation · advisory and safety checked">
              {recommendation ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div className="zone-stats">
              <div>
                <div className="stat-label">Current Setpoint</div>
                <div className="stat-value">{state.hvac_status.present_value.toFixed(1)}°C</div>
              </div>
              <div>
                <div className="stat-label">Recommended</div>
                <div className="stat-value" style={{ color: "var(--accent-blue)" }}>
                  {recommendation.validation.validated_setpoint?.toFixed(1) ?? "—"}{recommendation.validation.validated_setpoint !== null ? "°C" : ""}
                </div>
                <span className={`badge ${recommendation.validation.outcome === "REJECTED" ? "danger" : "success"}`}>
                  {recommendation.validation.outcome}
                </span>
              </div>
              <div>
                <div className="stat-label">Expected Power</div>
                <div className="stat-value">{recommendation.deterministic_recommendation?.expected_power_kw.toFixed(2) ?? "—"} kW</div>
              </div>
              <div>
                <div className="stat-label">Est. Hourly Cost</div>
                <div className="stat-value">{recommendation.deterministic_recommendation?.estimated_hourly_cost.toFixed(4) ?? "—"}</div>
              </div>
            </div>

            <div className="section-block">
              <div className="stat-label" style={{ marginBottom: "0.25rem" }}>Reason</div>
              <div style={{ fontSize: "0.875rem", color: "var(--text-secondary)" }}>
                {recommendation.recommendation_kind === "intelligence"
                  ? recommendation.intelligence_recommendation?.rationale
                  : recommendation.deterministic_recommendation?.reason ?? recommendation.validation.rejection_reason}
              </div>
              <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.375rem" }}>
                {recommendation.recommendation_kind === "intelligence" ? "Intelligence advisory" : recommendation.recommendation_kind === "deterministic_fallback" ? "Deterministic fallback" : "Rejected"}
                {recommendation.recommendation_kind === "intelligence" && recommendation.intelligence_recommendation
                  ? ` · ${recommendation.intelligence_recommendation.provider} · confidence ${recommendation.intelligence_recommendation.confidence}` : ""}
              </div>
              {recommendation.validation.fallback_reason && <div className="badge warning">Fallback: {recommendation.validation.fallback_reason}</div>}
              {recommendation.validation.rejection_reason && <div className="badge danger">Rejected: {recommendation.validation.rejection_reason}</div>}
            </div>

            {canOperate && <Button variant="primary" onClick={applyRecommendation} disabled={loading || recommendation.validation.validated_setpoint === null}>
              {loading ? "Applying..." : "Apply Validated Recommendation (Simulated Control)"}
            </Button>}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <div style={{ fontSize: "0.875rem", color: "var(--text-muted)" }}>
              No recommendation generated yet for this zone.
            </div>
            {canOperate && <Button variant="neutral" onClick={generateRecommendation} disabled={loading}>
              {loading ? "Generating..." : "Generate Recommendation"}
            </Button>}
          </div>
        )}
      </Card>

      {/* Error message */}
      {error && (
        <div style={{ color: "var(--accent-red)", fontSize: "0.875rem", padding: "0.5rem 0" }}>
          {error}
        </div>
      )}

      {/* Event Timeline */}
      <Timeline events={history} />
    </div>
  );
}

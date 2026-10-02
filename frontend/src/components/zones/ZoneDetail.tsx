import { useEffect, useState } from "react";
import { useZoneState } from "../../hooks/useZoneState";
import { Card, OccupancyBadge, Button, ErrorState } from "../common";
import { Timeline } from "../events/Timeline";
import { CVPanel } from "../occupancy/CVPanel";
import { api, ApiRequestError } from "../../services/api";

function attributionMessage(reason: string | null, label: string) {
  if (!reason) return `${label} unavailable — insufficient validated telemetry`;
  if (reason.includes("TARIFF")) return `${label} unavailable — no valid tariff coverage`;
  if (reason.includes("ENERGY")) return `${label} unavailable — no validated energy boundaries`;
  return `${label} unavailable — insufficient validated telemetry`;
}

export function ZoneDetail({ zoneId, demoPhase, canOperate = true }: { zoneId: string | null; demoPhase?: string | null; canOperate?: boolean }) {
  const [modeBusy, setModeBusy] = useState(false);
  const [modeError, setModeError] = useState<ApiRequestError | Error | null>(null);
  const [policyStatus, setPolicyStatus] = useState<Awaited<ReturnType<typeof api.getCommandPolicyStatus>> | null>(null);
  const {
    state,
    history,
    recommendation,
    controlResult,
    optimizationIntervals,
    loading,
    error,
    refresh,
    generateRecommendation,
    applyRecommendation,
  } = useZoneState(zoneId);

  useEffect(() => {
    let active = true;
    setPolicyStatus(null);
    if (zoneId && canOperate) api.getCommandPolicyStatus().then(value => { if (active) setPolicyStatus(value); })
      .catch(() => { if (active) setPolicyStatus(null); });
    return () => { active = false; };
  }, [zoneId, canOperate]);

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
    return <div className="zone-detail-skeleton" aria-label="Loading zone details"><span /><span /><span /><span /></div>;
  }

  if (error || !state) {
    const title = error?.includes("403") ? "This account cannot access the selected zone"
      : error?.includes("404") ? "This zone is no longer available"
      : error?.includes("401") ? "Your session needs attention"
      : "Zone information is unavailable";
    return <ErrorState title={title} onRetry={() => void refresh()} details={<code>{error || "No zone state was returned."}</code>}>
      The current zone snapshot could not be loaded. Existing control remains governed by the backend safety checks.
    </ErrorState>;
  }

  const { zone, occupancy, temperature, energy, tariff, hvac_status } = state;
  const controlMode = state.control_mode;
  const controlBlockedReason = !canOperate ? "Operational access is required to issue a control command."
    : controlMode?.manual_override ? "Manual override is active; AuraTwin commands are paused."
    : !controlMode?.control_enabled ? "Autonomous control is disabled for this zone."
    : controlMode.fail_safe_active || controlMode.provider_failure_latched ? "Fail-safe is active; operator recovery and fresh validation are required."
    : policyStatus?.ready !== true ? "Control is locked until command-limit policy readiness is confirmed."
    : null;

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
              <Button disabled={modeBusy || (!controlMode.control_enabled && policyStatus?.ready !== true)} variant={controlMode.control_enabled ? "neutral" : "primary"}
                onClick={() => updateMode(() => api.setZoneControlEnabled(zoneId, !controlMode.control_enabled))}>
                {controlMode.control_enabled ? "Disable Autonomous Control" : "Re-enable Autonomous Control"}
              </Button>
            </div>
          )}
          <div style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginTop: "0.35rem" }}>
            Manual override pauses AuraTwin commands; it does not send a manual HVAC setpoint.
          </div>
          {!controlMode?.control_enabled && policyStatus && !policyStatus.ready && <div className="safety-lock-panel" role="status">
            <strong>🔒 HVAC CONTROL LOCKED</strong>
            <p>AuraTwin has paused automatic HVAC control because the required safety policy is not fully configured.</p>
            <details><summary>View safety details</summary><div className="safety-technical"><span>Safety command-limit policy is {policyStatus.reason_code === "COMMAND_POLICY_INCOMPLETE" ? "incomplete" : "invalid"}.</span>
              {!!policyStatus.missing_configuration.length && <span>Missing: {policyStatus.missing_configuration.join(", ")}</span>}
              {!!policyStatus.invalid_configuration.length && <span>Invalid: {policyStatus.invalid_configuration.join(", ")}</span>}
            </div></details>
          </div>}
          {!controlMode?.control_enabled && !policyStatus && canOperate && <div role="status" style={{ marginTop: ".5rem", color: "var(--text-muted)" }}>
            Control readiness could not be verified. Re-enable remains disabled until the backend policy status is available.
          </div>}
          {modeError && <div className={modeError instanceof ApiRequestError && modeError.code ? undefined : "demo-error"}
            role={modeError instanceof ApiRequestError && modeError.code ? "status" : "alert"}
            style={{ marginTop: "0.5rem", ...(modeError instanceof ApiRequestError && modeError.code ? { borderLeft: "3px solid var(--warning, #d29922)", padding: ".65rem", background: "var(--surface-elevated)", borderRadius: 6 } : {}) }}>
            {modeError instanceof ApiRequestError && modeError.code === "COMMAND_POLICY_INCOMPLETE" ? <>
              <strong>🔒 HVAC CONTROL LOCKED</strong><br />AuraTwin cannot enable automatic control until its required safety limits are configured.
              <details><summary>View safety details</summary><div className="safety-technical">
                {!!modeError.missingConfiguration?.length && <span>Missing: {modeError.missingConfiguration.join(", ")}</span>}
                {modeError.status && <small>HTTP {modeError.status} · {modeError.endpoint}</small>}
              </div></details>
            </> : modeError instanceof ApiRequestError && modeError.code === "COMMAND_POLICY_INVALID" ? <>
              <strong>🔒 HVAC CONTROL LOCKED</strong><br />The configured safety command limits need correction before control can be enabled.
              <details><summary>View safety details</summary><div className="safety-technical">
                {!!modeError.invalidConfiguration?.length && <span>Invalid: {modeError.invalidConfiguration.join(", ")}</span>}
                {modeError.status && <small>HTTP {modeError.status} · {modeError.endpoint}</small>}
              </div></details>
            </> : modeError instanceof ApiRequestError && modeError.code ? <><strong>{modeError.code.replaceAll("_", " ")}</strong><br />{modeError.message}</> : <>{modeError.message}</>}
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
      <section className="optimization-lifecycle">
        {optimizationIntervals?.active ? <article className="optimization-interval-card active-interval">
          <div className="optimization-card-header"><div><p className="eyebrow">AURATWIN OPTIMIZATION</p><h2>Active optimization</h2></div><span className="badge success">HOLDING OPTIMIZED SETPOINT</span></div>
          <div className="optimization-interval-grid">
            <div><small>OCCUPANCY</small><strong>{optimizationIntervals.active.starting_occupancy} people</strong></div>
            <div><small>ROOM TEMPERATURE</small><strong>{optimizationIntervals.active.starting_temperature.toFixed(1)}°C</strong></div>
            <div><small>HVAC SETPOINT</small><strong>{optimizationIntervals.active.previous_setpoint.toFixed(1)}°C <span>→</span> {optimizationIntervals.active.optimized_setpoint.toFixed(1)}°C</strong></div>
            <div><small>STARTED</small><strong>{new Date(optimizationIntervals.active.started_at).toLocaleString()}</strong></div>
            <div><small>DURATION</small><strong>{Math.max(0, Math.floor((Date.now() - Date.parse(optimizationIntervals.active.started_at)) / 60000))} min</strong></div>
            <div><small>NEXT EVALUATION</small><strong>Waiting for next occupancy change</strong></div>
          </div>
          <div className="interval-impact"><div><small>ENERGY CONSUMED</small><strong>{optimizationIntervals.active.energy_consumed_kwh == null ? attributionMessage(optimizationIntervals.active.energy_reason_code, "Energy impact") : `${optimizationIntervals.active.energy_consumed_kwh.toFixed(3)} kWh`}</strong></div>
            <div><small>COST CONSUMED</small><strong>{optimizationIntervals.active.cost_consumed == null ? attributionMessage(optimizationIntervals.active.cost_reason_code, "Cost impact") : `${optimizationIntervals.active.currency ?? ""} ${optimizationIntervals.active.cost_consumed.toFixed(3)}`}</strong></div>
            {optimizationIntervals.active.simulated && <span className="badge warning">SIMULATED · NOT METER DATA</span>}
            <span className="badge neutral">SAVINGS NOT YET MEASURABLE · NO VALIDATED COMPARISON BASELINE</span>
            <small>Stored in database</small>
          </div>
        </article> : null}
        {optimizationIntervals?.completed.slice(0, 3).map(interval => <article className="optimization-interval-card completed-interval" key={interval.interval_id}>
          <div className="optimization-card-header"><div><p className="eyebrow">OPTIMIZATION COMPLETE</p><h2>{interval.starting_occupancy} → {interval.ending_occupancy ?? "—"} people</h2></div><span className="badge neutral">COMPLETED</span></div>
          <div className="interval-impact"><div><small>HVAC SETPOINT</small><strong>{interval.previous_setpoint.toFixed(1)}°C → {interval.optimized_setpoint.toFixed(1)}°C</strong></div>
            <div><small>DURATION</small><strong>{interval.duration_seconds == null ? "—" : `${Math.round(interval.duration_seconds / 60)} min`}</strong></div>
            <div><small>ENERGY CONSUMED</small><strong>{interval.energy_consumed_kwh == null ? attributionMessage(interval.energy_reason_code, "Energy impact") : `${interval.energy_consumed_kwh.toFixed(3)} kWh`}</strong></div>
            <div><small>COST CONSUMED</small><strong>{interval.cost_consumed == null ? attributionMessage(interval.cost_reason_code, "Cost impact") : `${interval.currency ?? ""} ${interval.cost_consumed.toFixed(3)}`}</strong></div>
            <div><small>SAVINGS</small><strong>Not yet measurable — no validated comparison baseline</strong></div>
            <div><small>COMPLETED</small><strong>{interval.ended_at ? new Date(interval.ended_at).toLocaleString() : "—"}</strong></div>
            {interval.simulated && <span className="badge warning">SIMULATED · NOT METER DATA</span>}
          </div>
        </article>)}
      </section>
      <Card title="AI recommendation · safety checked">
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
                  {recommendation.validation.outcome === "REJECTED" ? "PAUSED · SAFETY CHECK" : recommendation.validation.outcome}
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
              {recommendation.validation.rejection_reason && <div className="optimization-rejected" role="status">
                <strong>OPTIMIZATION PAUSED</strong>
                <span>{recommendation.validation.rejection_reason.includes("OCCUPANCY")
                  ? "AuraTwin is waiting for a valid occupancy observation."
                  : recommendation.validation.rejection_reason.includes("TEMPERATURE")
                    ? "AuraTwin is waiting for a valid temperature observation."
                    : "This recommendation did not pass the required safety checks."}</span>
                <details><summary>View technical reason</summary><code>{recommendation.validation.rejection_reason}</code></details>
              </div>}
            </div>

            {canOperate && <Button variant={controlBlockedReason || recommendation.validation.validated_setpoint === null ? "neutral" : "primary"} onClick={applyRecommendation} disabled={loading || recommendation.validation.validated_setpoint === null || Boolean(controlBlockedReason)}>
              {loading ? "Applying..." : controlBlockedReason ? "Control locked" : "Apply Validated Recommendation (Simulated Control)"}
            </Button>}
            {controlBlockedReason && <p className="control-lock-reason" role="status">{controlBlockedReason}</p>}
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
        <div className={error.includes("holding") ? "optimization-holding-notice" : "optimization-request-error"}
          role={error.includes("holding") ? "status" : "alert"}>
          {error}
        </div>
      )}

      {/* Event Timeline */}
      <Timeline events={history} />
    </div>
  );
}

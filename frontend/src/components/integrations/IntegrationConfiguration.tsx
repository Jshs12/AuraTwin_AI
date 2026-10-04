import { useCallback, useEffect, useState } from "react";
import { api } from "../../services/api";

type Integration = { integration_id: string; name: string; integration_type: string; status: string; simulated: boolean; connection_state?: string; commissioning_state?: string };
type Device = { device_id: string; name: string; external_device_id: string; device_type: string; manufacturer?: string | null; model?: string | null; zone_id?: string | null; status?: string };
type Point = { point_mapping_id: string; external_point_id: string; logical_signal: string; unit?: string | null; readable?: boolean; writable?: boolean; zone_id: string | null; mapping_status: string; mapping_confidence: number | null; mapping_source: string | null; latestObservation?: Record<string, unknown> | null };
type Commissioning = { state: string; read_only_ready: boolean; message: string; active_devices: number; confirmed_readable_mappings: number; observation_quality: string[]; simulated: boolean };
type IntegrationHealth = { connection_state: string; last_seen_at?: string | null; last_error?: string | null; physical_connection_implemented: boolean; observation_status?: string; last_observation?: Record<string, unknown> | null; signals: Array<{ signal: string; quality_state: string; source?: string | null; simulated?: boolean | null }> };
type EdgeStatus = { edge_id: string | null; building_id: string; version: string; mode: "simulated" | "real"; state: string; transport_state: string; queue_depth: number; max_buffer_messages: number; last_heartbeat: string | null; last_observation_forwarded: string | null; simulated: boolean; capabilities: string[]; healthy: boolean; reason_code: string | null };

export function IntegrationConfiguration({ buildingId, zones }: { buildingId?: string; zones: Array<{ zone_id: string; name: string }> }) {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [points, setPoints] = useState<Point[]>([]);
  const [selectedIntegration, setSelectedIntegration] = useState("");
  const [selectedDevice, setSelectedDevice] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testReport, setTestReport] = useState("");
  const [controlPolicy, setControlPolicy] = useState<{ ready: boolean; missing_configuration: string[]; invalid_configuration: string[] } | null>(null);
  const [commissioning, setCommissioning] = useState<Commissioning | null>(null);
  const [health, setHealth] = useState<IntegrationHealth | null>(null);
  const [edgeStatus, setEdgeStatus] = useState<EdgeStatus | null>(null);
  const [edgeStatusUnavailable, setEdgeStatusUnavailable] = useState(false);
  const [observationResults, setObservationResults] = useState<Array<Record<string, unknown>>>([]);

  const loadPoints = async (deviceId: string): Promise<Point[]> => {
    const items = await api.getDevicePoints(deviceId) as Point[];
    return Promise.all(items.map(async point => ({ ...point,
      latestObservation: await api.getPointLatestObservation(point.point_mapping_id),
    })));
  };

  const refreshIntegrations = useCallback(async () => {
    if (!buildingId) { setIntegrations([]); return; }
    try { const items = await api.getIntegrations(buildingId); setIntegrations(items); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to load integrations"); }
  }, [buildingId]);
  useEffect(() => { void refreshIntegrations(); }, [refreshIntegrations]);
  useEffect(() => { setControlPolicy(null); api.getCommandPolicyStatus().then(setControlPolicy).catch(() => setControlPolicy(null)); }, [buildingId]);
  useEffect(() => {
    if (!buildingId) { setEdgeStatus(null); setEdgeStatusUnavailable(false); return; }
    setEdgeStatus(null); setEdgeStatusUnavailable(false);
    api.getEdgeStatus(buildingId).then(setEdgeStatus).catch(() => setEdgeStatusUnavailable(true));
  }, [buildingId]);
  useEffect(() => {
    if (!selectedIntegration) { setDevices([]); return; }
    api.getIntegrationDevices(selectedIntegration).then(setDevices).catch(e => setError(e.message));
  }, [selectedIntegration]);
  useEffect(() => {
    if (!selectedIntegration) { setCommissioning(null); setHealth(null); return; }
    Promise.all([api.getIntegrationCommissioning(selectedIntegration), api.getIntegrationHealth(selectedIntegration)])
      .then(([state, status]) => { setCommissioning(state); setHealth(status); })
      .catch(e => setError(e instanceof Error ? e.message : "Unable to load commissioning status"));
  }, [selectedIntegration, integrations, devices, points]);
  useEffect(() => {
    if (!selectedDevice) { setPoints([]); return; }
    loadPoints(selectedDevice).then(setPoints).catch(e => setError(e.message));
  }, [selectedDevice]);

  if (!buildingId) return <div className="card">Select an authorized building to configure integrations.</div>;
  const runtimeSignals = new Set(["occupancy", "temperature", "cooling_setpoint"]);
  const run = async (action: () => Promise<unknown>) => {
    setError(""); setNotice("");
    try { await action(); setNotice("Configuration updated."); await refreshIntegrations();
      if (selectedIntegration) setDevices(await api.getIntegrationDevices(selectedIntegration));
      if (selectedDevice) setPoints(await loadPoints(selectedDevice));
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
  };
  const simulateObservation = async (point: Point) => {
    const raw = window.prompt(`SIMULATED observation for ${point.logical_signal}; enter a demo value`);
    if (raw === null) return;
    const value = Number(raw);
    if (!Number.isFinite(value)) { setError("Enter a finite numeric demo value."); return; }
    setError(""); setNotice("");
    try {
      const result = await api.createSimulatedPointObservation(
        point.point_mapping_id, value, new Date().toISOString());
      setObservationResults([{ ...result, logical_signal: point.logical_signal,
        mapping_status: point.mapping_status, point_mapping_id: point.point_mapping_id }]);
      if (selectedDevice) setPoints(await loadPoints(selectedDevice));
      setNotice(result.runtime_input_applied
        ? "SIMULATED observation accepted; current ZoneState updated after quality and freshness checks."
        : `SIMULATED observation persisted as history only${result.reason_code ? ` · ${result.reason_code}` : ""}.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Simulated observation was rejected"); }
  };
  const createIntegration = () => {
    const name = window.prompt("Integration name");
    if (!name) return;
    const type = window.prompt("Type: BACNET, CAMERA, or ENERGY_METER", "BACNET");
    if (!type || !["BACNET", "CAMERA", "ENERGY_METER"].includes(type.toUpperCase())) { setError("Choose BACNET, CAMERA, or ENERGY_METER."); return; }
    const fields = window.prompt("Non-secret configuration as JSON (example: {\"host\":\"127.0.0.1\",\"port\":47808})", "{}");
    if (fields === null) return;
    try { void run(async () => { const created = await api.createIntegration(buildingId, { name, integration_type: type.toUpperCase(), configuration: JSON.parse(fields) }); setSelectedIntegration(created.integration_id); }); }
    catch { setError("Configuration must be valid JSON."); }
  };
  const createDevice = () => {
    const name = window.prompt("Device name"); if (!name) return;
    const id = window.prompt("External device identifier"); if (!id) return;
    const kind = window.prompt("Device type", "CONTROLLER"); if (!kind) return;
    const manufacturer = window.prompt("Manufacturer (optional)") || null;
    const model = window.prompt("Model (optional)") || null;
    const zoneChoice = window.prompt(`Optional assigned zone number:\n${zones.map((zone, i) => `${i + 1}: ${zone.name}`).join("\n")}\nLeave blank for none`);
    const zone = zoneChoice ? zones[Number(zoneChoice) - 1] : null;
    if (zoneChoice && !zone) { setError("Select a valid zone number or leave blank."); return; }
    void run(async () => { const created = await api.createIntegrationDevice(selectedIntegration, { name, external_device_id: id, device_type: kind, manufacturer, model, zone_id: zone?.zone_id ?? null }); setSelectedDevice(created.device_id); });
  };
  const createPoint = () => {
    if (!zones.length) { setError("This building has no configured zones to map."); return; }
    const external = window.prompt("External point identifier"); if (!external) return;
    const signal = window.prompt("AuraTwin logical signal (e.g. temperature, occupancy, power, cooling_setpoint)"); if (!signal) return;
    const zoneHint = zones.map((zone, index) => `${index + 1}: ${zone.name}`).join("\n");
    const choice = window.prompt(`Map explicitly to zone number:\n${zoneHint}`, "1");
    const zone = zones[Number(choice) - 1];
    if (!zone) { setError("Select a valid zone number."); return; }
    const unit = window.prompt("Unit (optional; use the unit expected for this signal)") || null;
    void run(() => api.createDevicePoint(selectedDevice, { zone_id: zone.zone_id, external_point_id: external, logical_signal: signal, data_type: "number", unit, readable: true, writable: false }));
  };
  const editIntegration = (item: Integration) => {
    const name = window.prompt("Integration name", item.name); if (!name) return;
    void run(() => api.updateIntegration(item.integration_id, { name }));
  };
  const editDevice = (device: Device) => {
    const name = window.prompt("Device name", device.name); if (!name) return;
    const manufacturer = window.prompt("Manufacturer (blank keeps current)", device.manufacturer ?? "");
    const model = window.prompt("Model (blank keeps current)", device.model ?? "");
    if (manufacturer === null || model === null) return;
    void run(() => api.updateIntegrationDevice(device.device_id, { name, manufacturer: manufacturer || null, model: model || null }));
  };
  const editPoint = (point: Point) => {
    const signal = window.prompt("Logical signal", point.logical_signal); if (!signal) return;
    const zoneHint = zones.map((zone, index) => `${index + 1}: ${zone.name}`).join("\n");
    const zoneChoice = window.prompt(`Zone number (blank to unassign):\n${zoneHint}`, String(Math.max(0, zones.findIndex(zone => zone.zone_id === point.zone_id) + 1)));
    if (zoneChoice === null) return;
    const zone = zoneChoice ? zones[Number(zoneChoice) - 1] : null;
    if (zoneChoice && !zone) { setError("Select a valid zone number or leave blank."); return; }
    void run(() => api.updateDevicePoint(point.point_mapping_id, { logical_signal: signal, zone_id: zone?.zone_id ?? null }));
  };

  return <section style={{ display: "grid", gap: "1rem" }}>
    <div className="card" aria-label="Edge Connector Foundation status">
      <div className="card-title">EDGE FOUNDATION</div>
      {edgeStatus ? <>
        <p>{edgeStatus.reason_code === "EDGE_IDENTITY_NOT_CONFIGURED" || edgeStatus.reason_code === "EDGE_NOT_CONFIGURED_FOR_BUILDING"
          ? "EDGE NOT CONFIGURED FOR THIS BUILDING"
          : edgeStatus.simulated ? "SIMULATED EDGE" : edgeStatus.mode === "real" ? "REAL MODE · TRANSPORT UNAVAILABLE" : "EDGE UNAVAILABLE"} · {edgeStatus.state} · building {edgeStatus.building_id}</p>
        <p>Transport: {edgeStatus.transport_state} · queue {edgeStatus.queue_depth}/{edgeStatus.max_buffer_messages}</p>
        <p>Last local heartbeat: {edgeStatus.last_heartbeat ?? "not started"} · last forwarded observation: {edgeStatus.last_observation_forwarded ?? "none"}</p>
        <small>Capabilities: {edgeStatus.capabilities.length ? edgeStatus.capabilities.join(", ") : "none"}. No HVAC write or cloud control channel is available.</small>
        {edgeStatus.reason_code && <p role="status">Status reason: {edgeStatus.reason_code}</p>}
      </> : <p>{edgeStatusUnavailable ? "Edge status is unavailable." : "Loading Edge Connector status…"} No cloud connection is implied.</p>}
    </div>
    <div className="card">
      <div className="card-title">INTEGRATIONS · CONFIGURATION ONLY</div>
      <p>Building-scoped, supervised read-only adapter actions. Physical connectivity is unavailable until a protocol driver is configured.</p>
      <div className="section-block" aria-label="Integration onboarding lifecycle">
        <div className="card-title">ONBOARDING LIFECYCLE</div>
        <ol style={{ margin: 0, paddingLeft: "1.25rem", display: "grid", gap: ".3rem", color: "var(--text-secondary)" }}>
          <li>Organization → building → floor → zone: organization provisioned; structure configured above.</li>
          <li>Integration: {integrations.length ? `${integrations.length} configured` : "not configured"} · CONFIGURATION ONLY · NOT CONNECTED.</li>
          <li>Configuration validation is local only. Adapter connection attempts are explicit and bounded; BACnet/IP, RTSP, and meter drivers are currently NOT CONFIGURED.</li>
          <li>Devices: {devices.length} shown for selected integration · manually configured or SIMULATED FIXTURE.</li>
          <li>Points: {points.length} shown for selected device · {points.filter(point => point.mapping_status === "CONFIRMED").length} confirmed.</li>
          <li>Runtime: simulated explicit observations only; state applies after quality/freshness validation.</li>
        </ol>
        <div className="historical-metrics" style={{ marginTop: ".75rem" }}>
          <div><small>PHYSICAL ADAPTERS</small><strong>NOT CONFIGURED · READ ONLY · NO WRITES</strong></div>
          <div><small>DEVICE DISCOVERY</small><strong>SIMULATED FIXTURES · NO NETWORK I/O</strong></div>
          <div><small>RUNTIME OBSERVATIONS</small><strong>SIMULATED ONLY</strong></div>
          <div><small>CONTROL SAFETY</small><strong>{controlPolicy?.ready ? "POLICY CONFIGURED · BACKEND GATES ACTIVE" : controlPolicy ? `UNAVAILABLE · ${[...controlPolicy.missing_configuration, ...controlPolicy.invalid_configuration].join(", ")}` : "STATUS UNAVAILABLE · BACKEND GATES ACTIVE"}</strong></div>
        </div>
      </div>
      <button onClick={createIntegration}>Add integration</button>
      {integrations.map(item => <div key={item.integration_id} style={{ padding: ".65rem 0", borderBottom: "1px solid var(--border-color)" }}>
        <button onClick={() => { setSelectedIntegration(item.integration_id); setSelectedDevice(""); }}>{item.name}</button>
        <span style={{ marginLeft: ".75rem" }}>{item.integration_type} · {item.status} · {item.connection_state ?? "DISCONNECTED"} · {item.commissioning_state ?? "CONFIGURED"} · READ-ONLY</span>
        <button style={{ marginLeft: ".5rem" }} onClick={() => editIntegration(item)}>Edit</button>
        <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.testIntegration(item.integration_id).then(report => {
          setTestReport(`${report.result} · simulated=${report.simulated} · connection established=${report.connection_established}`);
        }))}>Validate configuration</button>
        {(item.connection_state ?? "DISCONNECTED") !== "CONNECTED" && item.status === "CONFIGURED" && <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.connectIntegration(item.integration_id).then(report => {
          setTestReport(`Adapter ${report.connection_state} · ${report.error_code ?? (report.connection_established ? "connection test succeeded" : "Physical adapter not configured")} · READ-ONLY · writes=${report.capabilities?.can_write ?? false}`);
        }))}>Test adapter connection</button>}
        {item.connection_state === "CONNECTED" && <>
          <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.pollIntegration(item.integration_id).then((report: any) => {
            setObservationResults(report.observations ?? []);
            const accepted = (report.observations ?? []).filter((row: any) => row.accepted).length;
            setTestReport(`One read-only poll · ${accepted}/${report.observations.length} accepted · ${report.simulated ? "SIMULATED" : "PROVIDER REPORTED"}`);
          }))}>Poll once</button>
          <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.disconnectIntegration(item.integration_id))}>Disconnect</button>
        </>}
        <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.discoverIntegration(item.integration_id).then(report => {
          setTestReport(`${report.message} · ${report.devices.length} ${report.simulated ? "fixture" : "read-only adapter"} device(s), ${report.points.length} point(s)`);
        }))}>{item.connection_state === "CONNECTED" ? "Discover devices (read-only)" : "Load simulated fixtures"}</button>
        {item.status !== "DISABLED" && <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.disableIntegration(item.integration_id))}>Disable</button>}
      </div>)}
      {testReport && <p role="status">{testReport}</p>}
      {observationResults.length > 0 && <div aria-label="Read-only observation results">
        <strong>READ-ONLY OBSERVATION RESULTS</strong>
        {observationResults.map((row, index) => <small key={`${String(row.point_mapping_id ?? index)}-${index}`} style={{ display: "block", marginTop: ".35rem" }}>
          {String(row.logical_signal ?? row.signal ?? "unknown signal")} · {row.accepted ? "ACCEPTED" : "REJECTED"}
          {row.value !== undefined ? ` · ${String(row.value)} ${String(row.unit ?? "")}` : ""}
          {row.quality_state ? ` · quality ${String(row.quality_state)}` : ""}
          {row.observed_at ? ` · observed ${String(row.observed_at)}` : ""}
          {row.ingested_at ? ` · ingested ${String(row.ingested_at)}` : ""}
          {row.source ? ` · ${String(row.source)}` : ""}
          {row.protocol ? ` · protocol ${String(row.protocol)}` : ""}
          {row.simulated === true ? " · SIMULATED" : row.simulated === false ? " · PROVIDER REPORTED" : ""}
          {row.mapping_status ? ` · mapping ${String(row.mapping_status)}` : ""}
          {row.runtime_applicable !== undefined ? ` · runtime ${row.runtime_input_applied ? "APPLIED" : row.runtime_applicable ? "NOT APPLIED" : "HISTORICAL ONLY"}` : ""}
          {row.reason_code ? ` · reason ${String(row.reason_code)}` : ""}
        </small>)}
      </div>}
      {selectedIntegration && <div className="section-block" aria-label="Integration commissioning status">
        <div className="card-title">COMMISSIONING · READ ONLY</div>
        <button onClick={() => void run(() => api.evaluateIntegrationCommissioning(selectedIntegration)
          .then(report => setCommissioning(report)))}>Evaluate commissioning</button>
        <p>{commissioning?.state ?? "Loading"} · {commissioning?.message ?? "Status unavailable"}</p>
        <p>Connection: {health?.connection_state ?? "unknown"} · {health?.physical_connection_implemented ? "VERIFIED ADAPTER" : "SIMULATED / NO PHYSICAL ADAPTER"}</p>
        <p>Observation status: {health?.observation_status ?? "unknown"} · last successful observation: {health?.last_seen_at ?? "none"} · last error: {health?.last_error ?? "none"}</p>
        {health?.last_observation && <small>Last attempt: {String(health.last_observation.accepted ? "ACCEPTED" : "REJECTED")} · {String(health.last_observation.signal ?? health.last_observation.logical_signal ?? "signal unavailable")} · quality {String(health.last_observation.quality_state ?? "unknown")} · {health.last_observation.simulated === true ? "SIMULATED" : "PROVIDER REPORTED / UNKNOWN"} · {String(health.last_observation.reason_code ?? "no rejection reason")}</small>}
        <p>Devices: {commissioning?.active_devices ?? 0} · confirmed readable mappings: {commissioning?.confirmed_readable_mappings ?? 0}</p>
        {health?.signals.map(signal => <small key={signal.signal} style={{ display: "block" }}>{signal.signal}: {signal.quality_state} · {signal.source ?? "no source"} · {signal.simulated === null ? "no observation" : signal.simulated ? "SIMULATED" : "provider reported"}</small>)}
      </div>}
    </div>
    {selectedIntegration && <div className="card">
      <div className="card-title">DEVICES</div>
      <button onClick={createDevice}>Add device</button>
      {devices.map(device => <div key={device.device_id} style={{ padding: ".5rem 0" }}>
        <button onClick={() => setSelectedDevice(device.device_id)}>{device.name}</button> · {device.device_type} · {device.external_device_id}
        <button style={{ marginLeft: ".5rem" }} onClick={() => editDevice(device)}>Edit</button>
        <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.disableIntegrationDevice(device.device_id))}>Disable</button>
      </div>)}
    </div>}
    {selectedDevice && <div className="card">
      <div className="card-title">POINTS AND LOGICAL MAPPINGS</div>
      <button onClick={createPoint}>Add point / mapping</button>
      {integrations.find(item => item.integration_id === selectedIntegration)?.connection_state === "CONNECTED" && <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.discoverDevicePoints(selectedDevice).then(report => {
        setTestReport(`Read-only point discovery · ${report.points.length} point(s) · ${report.simulated ? "SIMULATED" : "REAL PROVIDER"} · suggestions require operator confirmation`);
      }))}>Discover points (read-only)</button>}
      {points.map(point => <div key={point.point_mapping_id} style={{ display: "grid", gap: ".4rem", padding: ".6rem 0", borderBottom: "1px solid var(--border-color)" }}>
        <span>{point.external_point_id} → {point.logical_signal} · zone {zones.find(zone => zone.zone_id === point.zone_id)?.name ?? "unassigned"} · {point.mapping_status} · {runtimeSignals.has(point.logical_signal) ? "RUNTIME-CAPABLE AFTER VALIDATION" : "HISTORICAL-ONLY"} · unit {point.unit ?? "unspecified"} · {point.readable ? "readable" : "not readable"} · {point.writable ? "writable" : "read-only"} · source {point.mapping_source ?? "unknown"}{point.mapping_confidence === null ? "" : ` · confidence ${point.mapping_confidence}`}</span>
        <small>{point.latestObservation ? `Latest: ${String(point.latestObservation.value)} ${String(point.latestObservation.unit)} · ${String(point.latestObservation.quality_state)} · observed ${String(point.latestObservation.observed_at)} · ingested ${String(point.latestObservation.ingested_at)} · ${point.latestObservation.simulated ? "SIMULATED" : "provider reported"} · ${String(point.latestObservation.source)}` : "No persisted observation"}</small>
        <div style={{ display: "flex", gap: ".5rem" }}>
        <button onClick={() => editPoint(point)}>Edit point / zone</button>
        {point.mapping_status === "CONFIRMED" && <button onClick={() => void simulateObservation(point)}>Add simulated observation</button>}
        {point.mapping_status !== "CONFIRMED" && <button onClick={() => void run(() => api.decidePointMapping(point.point_mapping_id, "confirm"))}>Confirm</button>}
        {point.mapping_status !== "REJECTED" && <button onClick={() => void run(() => api.decidePointMapping(point.point_mapping_id, "reject"))}>Reject</button>}
        {point.mapping_status !== "INACTIVE" && <button onClick={() => void run(() => api.deactivatePointMapping(point.point_mapping_id))}>Deactivate</button>}
        </div>
      </div>)}
    </div>}
    {error && <div role="alert" style={{ color: "var(--danger, #f85149)" }}>{error}</div>}
    {notice && <div role="status">{notice}</div>}
  </section>;
}

import { useCallback, useEffect, useState } from "react";
import { api } from "../../services/api";

type Integration = { integration_id: string; name: string; integration_type: string; status: string; simulated: boolean };
type Device = { device_id: string; name: string; external_device_id: string; device_type: string; manufacturer?: string | null; model?: string | null; zone_id?: string | null; status?: string };
type Point = { point_mapping_id: string; external_point_id: string; logical_signal: string; unit?: string | null; readable?: boolean; writable?: boolean; zone_id: string | null; mapping_status: string; mapping_confidence: number | null; mapping_source: string | null; latestObservation?: Record<string, unknown> | null };

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
    if (!selectedIntegration) { setDevices([]); return; }
    api.getIntegrationDevices(selectedIntegration).then(setDevices).catch(e => setError(e.message));
  }, [selectedIntegration]);
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
    <div className="card">
      <div className="card-title">INTEGRATIONS · CONFIGURATION ONLY</div>
      <p>Building-scoped connection metadata. No network connection or hardware discovery is performed.</p>
      <div className="section-block" aria-label="Integration onboarding lifecycle">
        <div className="card-title">ONBOARDING LIFECYCLE</div>
        <ol style={{ margin: 0, paddingLeft: "1.25rem", display: "grid", gap: ".3rem", color: "var(--text-secondary)" }}>
          <li>Organization → building → floor → zone: organization provisioned; structure configured above.</li>
          <li>Integration: {integrations.length ? `${integrations.length} configured` : "not configured"} · CONFIGURATION ONLY · NOT CONNECTED.</li>
          <li>Connection test: simulated configuration check only. Discovery: NOT IMPLEMENTED.</li>
          <li>Devices: {devices.length} shown for selected integration · manually configured.</li>
          <li>Points: {points.length} shown for selected device · {points.filter(point => point.mapping_status === "CONFIRMED").length} confirmed.</li>
          <li>Runtime: simulated explicit observations only; state applies after quality/freshness validation.</li>
        </ol>
        <div className="historical-metrics" style={{ marginTop: ".75rem" }}>
          <div><small>HARDWARE CONNECTION</small><strong>NOT IMPLEMENTED</strong></div>
          <div><small>DEVICE DISCOVERY</small><strong>NOT IMPLEMENTED</strong></div>
          <div><small>RUNTIME OBSERVATIONS</small><strong>SIMULATED ONLY</strong></div>
          <div><small>CONTROL SAFETY</small><strong>{controlPolicy?.ready ? "POLICY CONFIGURED · BACKEND GATES ACTIVE" : controlPolicy ? `UNAVAILABLE · ${[...controlPolicy.missing_configuration, ...controlPolicy.invalid_configuration].join(", ")}` : "STATUS UNAVAILABLE · BACKEND GATES ACTIVE"}</strong></div>
        </div>
      </div>
      <button onClick={createIntegration}>Add integration</button>
      {integrations.map(item => <div key={item.integration_id} style={{ padding: ".65rem 0", borderBottom: "1px solid var(--border-color)" }}>
        <button onClick={() => { setSelectedIntegration(item.integration_id); setSelectedDevice(""); }}>{item.name}</button>
        <span style={{ marginLeft: ".75rem" }}>{item.integration_type} · {item.status} · simulated metadata</span>
        <button style={{ marginLeft: ".5rem" }} onClick={() => editIntegration(item)}>Edit</button>
        <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.testIntegration(item.integration_id).then(report => {
          setTestReport(`${report.result} · simulated=${report.simulated} · connection established=${report.connection_established}`);
        }))}>Validate configuration</button>
        {item.status !== "DISABLED" && <button style={{ marginLeft: ".5rem" }} onClick={() => void run(() => api.disableIntegration(item.integration_id))}>Disable</button>}
      </div>)}
      {testReport && <p role="status">{testReport}</p>}
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
      {points.map(point => <div key={point.point_mapping_id} style={{ display: "grid", gap: ".4rem", padding: ".6rem 0", borderBottom: "1px solid var(--border-color)" }}>
        <span>{point.external_point_id} → {point.logical_signal} · zone {zones.find(zone => zone.zone_id === point.zone_id)?.name ?? "unassigned"} · {point.mapping_status} · {runtimeSignals.has(point.logical_signal) ? "RUNTIME-CAPABLE AFTER VALIDATION" : "HISTORICAL-ONLY"} · unit {point.unit ?? "unspecified"} · {point.readable ? "readable" : "not readable"} · {point.writable ? "writable" : "read-only"} · source {point.mapping_source ?? "unknown"}{point.mapping_confidence === null ? "" : ` · confidence ${point.mapping_confidence}`}</span>
        <small>{point.latestObservation ? `Latest: ${String(point.latestObservation.value)} ${String(point.latestObservation.unit)} · ${String(point.latestObservation.quality_state)} · ${point.latestObservation.simulated ? "SIMULATED" : "provider reported"} · ${String(point.latestObservation.source)}` : "No persisted observation"}</small>
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

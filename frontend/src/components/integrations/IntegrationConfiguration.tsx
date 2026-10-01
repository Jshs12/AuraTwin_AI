import { useCallback, useEffect, useState } from "react";
import { api } from "../../services/api";

type Integration = { integration_id: string; name: string; integration_type: string; status: string; simulated: boolean };
type Device = { device_id: string; name: string; external_device_id: string; device_type: string };
type Point = { point_mapping_id: string; external_point_id: string; logical_signal: string; mapping_status: string; mapping_confidence: number | null };

export function IntegrationConfiguration({ buildingId }: { buildingId?: string }) {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [points, setPoints] = useState<Point[]>([]);
  const [selectedIntegration, setSelectedIntegration] = useState("");
  const [selectedDevice, setSelectedDevice] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [testReport, setTestReport] = useState("");

  const refreshIntegrations = useCallback(async () => {
    if (!buildingId) { setIntegrations([]); return; }
    try { const items = await api.getIntegrations(buildingId); setIntegrations(items); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to load integrations"); }
  }, [buildingId]);
  useEffect(() => { void refreshIntegrations(); }, [refreshIntegrations]);
  useEffect(() => {
    if (!selectedIntegration) { setDevices([]); return; }
    api.getIntegrationDevices(selectedIntegration).then(setDevices).catch(e => setError(e.message));
  }, [selectedIntegration]);
  useEffect(() => {
    if (!selectedDevice) { setPoints([]); return; }
    api.getDevicePoints(selectedDevice).then(setPoints).catch(e => setError(e.message));
  }, [selectedDevice]);

  if (!buildingId) return <div className="card">Select an authorized building to configure integrations.</div>;
  const run = async (action: () => Promise<unknown>) => {
    setError(""); setNotice("");
    try { await action(); setNotice("Configuration updated."); await refreshIntegrations();
      if (selectedIntegration) setDevices(await api.getIntegrationDevices(selectedIntegration));
      if (selectedDevice) setPoints(await api.getDevicePoints(selectedDevice));
    } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
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
    void run(async () => { const created = await api.createIntegrationDevice(selectedIntegration, { name, external_device_id: id, device_type: kind }); setSelectedDevice(created.device_id); });
  };
  const createPoint = () => {
    const external = window.prompt("External point identifier"); if (!external) return;
    const signal = window.prompt("AuraTwin logical signal (e.g. temperature, occupancy, power, cooling_setpoint)"); if (!signal) return;
    void run(() => api.createDevicePoint(selectedDevice, { external_point_id: external, logical_signal: signal, data_type: "number", readable: true, writable: false }));
  };
  const editIntegration = (item: Integration) => {
    const name = window.prompt("Integration name", item.name); if (!name) return;
    void run(() => api.updateIntegration(item.integration_id, { name }));
  };
  const editDevice = (device: Device) => {
    const name = window.prompt("Device name", device.name); if (!name) return;
    void run(() => api.updateIntegrationDevice(device.device_id, { name }));
  };

  return <section style={{ display: "grid", gap: "1rem" }}>
    <div className="card">
      <div className="card-title">INTEGRATIONS · CONFIGURATION ONLY</div>
      <p>Building-scoped connection metadata. No network connection or hardware discovery is performed.</p>
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
      {points.map(point => <div key={point.point_mapping_id} style={{ display: "flex", gap: ".6rem", alignItems: "center", padding: ".5rem 0" }}>
        <span>{point.external_point_id} → {point.logical_signal} · {point.mapping_status}{point.mapping_confidence === null ? "" : ` · confidence ${point.mapping_confidence}`}</span>
        {point.mapping_status !== "CONFIRMED" && <button onClick={() => void run(() => api.decidePointMapping(point.point_mapping_id, "confirm"))}>Confirm</button>}
        {point.mapping_status !== "REJECTED" && <button onClick={() => void run(() => api.decidePointMapping(point.point_mapping_id, "reject"))}>Reject</button>}
      </div>)}
    </div>}
    {error && <div role="alert" style={{ color: "var(--danger, #f85149)" }}>{error}</div>}
    {notice && <div role="status">{notice}</div>}
  </section>;
}

import { useCallback, useEffect, useState } from "react";
import { useZones } from "../hooks/useZones";
import { useMonitoring } from "../hooks/useMonitoring";
import { TopNav } from "../components/layout/TopNav";
import { ZoneList } from "../components/zones/ZoneList";
import { ZoneDetail } from "../components/zones/ZoneDetail";
import { CVPanel } from "../components/occupancy/CVPanel";
import { Summary } from "../components/dashboard/Summary";
import { MonitoringPanel } from "../components/monitoring/MonitoringPanel";
import { EnergyChart } from "../components/energy/EnergyChart";
import { HistoricalTelemetry } from "../components/energy/HistoricalTelemetry";
import { EventStream } from "../components/events/EventStream";
import { DemoModePanel } from "../components/demo/DemoModePanel";
import type { DemoBuildingSummary } from "../types/api";
import { formatISTTimestamp } from "../utils/time";
import { AccessPanel } from "../components/security/AccessPanel";
import { AuditPanel } from "../components/security/AuditPanel";
import { api } from "../services/api";
import { IntegrationConfiguration } from "../components/integrations/IntegrationConfiguration";
import { BuildingOnboarding } from "../components/integrations/BuildingOnboarding";
import { KnowledgePanel } from "../components/knowledge/KnowledgePanel";
import { BuildingCommandCenter } from "../components/dashboard/BuildingCommandCenter";
import { defaultZoneSelection } from "../utils/zoneSelection";

type Section = "Overview" | "Zones" | "Occupancy" | "Energy" | "Knowledge" | "Events" | "Integrations" | "Access" | "Audit";

export function Dashboard({ role }: { role: "ADMIN" | "OPERATOR" }) {
  const [activeSection, setActiveSection] = useState<Section>("Overview");
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [demoSummary, setDemoSummary] = useState<DemoBuildingSummary | null>(null);
  const [buildings, setBuildings] = useState<Array<{ building_id: string; building_key: string; organization_id: string; name: string }>>([]);
  const [selectedBuildingId, setSelectedBuildingId] = useState<string | undefined>();
  const [buildingsError, setBuildingsError] = useState("");

  const refreshBuildings = useCallback(async () => {
    const items = await api.getBuildings();
    setBuildings(items);
    setSelectedBuildingId(current => current && items.some(item => item.building_id === current)
      ? current : items[0]?.building_id);
  }, []);
  useEffect(() => {
    refreshBuildings().catch(err => {
      setBuildings([]);
      setBuildingsError(err instanceof Error ? err.message : "Unable to load authorized buildings.");
    });
  }, [refreshBuildings]);

  // Centralized state from WebSocket + monitoring API
  const monitoring = useMonitoring();

  // Zone configuration is persistent and scoped to the selected authorized building.
  const { zones, loading: zonesLoading, error: zonesError } = useZones(selectedBuildingId);
  useEffect(() => {
    const nextSelection = defaultZoneSelection(zones, selectedZoneId);
    if (nextSelection !== selectedZoneId) setSelectedZoneId(nextSelection);
  }, [zones, selectedZoneId]);
  const updateDemoSummary = useCallback((summary: DemoBuildingSummary) => setDemoSummary(summary), []);
  const energyHistory = demoSummary?.scenario_id
    ? demoSummary.energy_history.map(sample => ({
        timestamp: sample.timestamp,
        time: formatISTTimestamp(sample.timestamp),
        power_kw: sample.power_kw, energy_kwh: sample.energy_kwh, occupancy: sample.occupancy, zone_id: "building",
      }))
    : monitoring.energyHistory;
  const displayPower = demoSummary?.scenario_id ? demoSummary.simulated_power_kw : monitoring.currentPower;

  return (
    <div className="app-shell">
      <TopNav
        activeSection={activeSection}
        onNavigate={(s) => setActiveSection(s as Section)}
        monitoringRunning={monitoring.status?.running ?? false}
        wsConnected={monitoring.connected}
        demoSimulation={monitoring.status?.demo_simulation ?? false}
        occupancyProvider={monitoring.status?.occupancy_provider ?? "unknown"}
        occupancyProviderReady={monitoring.status?.occupancy_provider_ready ?? false}
        role={role}
      />

      <main className="main-content">
        {buildingsError && <div className="demo-error" role="alert">{buildingsError}</div>}
        {buildings.length > 1 && <label className="building-selector">
          Building
          <select value={selectedBuildingId} onChange={event => {
            setSelectedBuildingId(event.target.value);
            setSelectedZoneId(null);
          }}>
            {buildings.map(building => <option key={building.building_id} value={building.building_id}>{building.name}</option>)}
          </select>
        </label>}
        {/* ── OVERVIEW ─────────────────────────────────────────── */}
        {activeSection === "Overview" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <BuildingCommandCenter
              buildingName={buildings.find(item => item.building_id === selectedBuildingId)?.name ?? ""}
              zones={zones} status={monitoring.status} events={monitoring.events}
              power={displayPower} energyHistory={energyHistory} demoSummary={demoSummary}
            />
            {role === "OPERATOR" && <DemoModePanel onSummary={updateDemoSummary} />}
            <Summary
              zones={zones}
              totalOccupancy={monitoring.totalOccupancy}
              occupiedZones={monitoring.occupiedZones}
              monitoringStatus={monitoring.status}
              currentPower={displayPower}
              demoSummary={demoSummary}
            />
            <MonitoringPanel
              status={monitoring.status}
              error={monitoring.error}
              connected={monitoring.connected}
              connectionStatus={monitoring.connectionStatus}
              onReconnect={monitoring.reconnectWebSocket}
              onStart={monitoring.startMonitoring}
              onStop={monitoring.stopMonitoring}
              canManage={role === "OPERATOR"}
            />
            <EnergyChart
              history={energyHistory}
              demoMode={Boolean(demoSummary?.scenario_id)}
            />
          </div>
        )}

        {/* ── ZONES ────────────────────────────────────────────── */}
        {activeSection === "Zones" && (
          <div className="zones-workspace">
            <div>
              {zonesLoading ? (
                <div className="card" style={{ color: "var(--text-muted)" }}>Loading zones…</div>
              ) : zonesError ? (
                <div className="card demo-error" role="alert">{zonesError}</div>
              ) : (
                <ZoneList
                  zones={zones}
                  selectedZoneId={selectedZoneId}
                  onSelectZone={setSelectedZoneId}
                  monitoringZones={monitoring.status?.zones ?? []}
                />
              )}
            </div>
            <div>
              {selectedZoneId ? (
                <ZoneDetail zoneId={selectedZoneId} demoPhase={monitoring.status?.demo_phase ?? null} canOperate={role === "OPERATOR"} />
              ) : (
                <div className="card product-empty-state" style={{ color: "var(--text-muted)", textAlign: "center", padding: "3rem" }}>
                  {zones.length ? "Choose a configured zone to inspect its state." : "No active zones are available for this building."}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── OCCUPANCY ────────────────────────────────────────── */}
        {activeSection === "Occupancy" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <section className="occupancy-overview card">
              <div className="section-header"><div><p className="eyebrow">PEOPLE & SPACE</p><h1>Occupancy</h1><p>Current zone snapshot · source depends on configured provider.</p></div><span className="badge warning">SIMULATED / PROVIDER REPORTED</span></div>
              <div className="occupancy-overview-grid">
                <div className="occupancy-total"><strong>{monitoring.totalOccupancy}</strong><span>people in monitored zones</span><small>{monitoring.occupiedZones} occupied · {monitoring.status?.zones_enabled ?? 0} monitored</small></div>
                <div className="occupancy-zone-breakdown">{(monitoring.status?.zones ?? []).map(item => {
                  const zone = zones.find(candidate => candidate.zone_id === item.zone_id);
                  const count = item.last_people_count;
                  const capacity = zone?.capacity ?? 0;
                  const percent = capacity > 0 ? Math.min(100, count / capacity * 100) : 0;
                  return <div key={item.zone_id} className="occupancy-bar-row"><span>{zone?.name ?? item.zone_id}</span><div className="bar-track"><i style={{ width: `${percent}%` }} /></div><b>{count}<small> / {capacity || "—"}</small></b></div>;
                })}{!monitoring.status?.zones.length && <p className="muted">Start monitoring to display zone occupancy snapshots.</p>}</div>
              </div>
              <p className="muted">Manual YOLO upload below is an independent test and does not feed autonomous demo monitoring.</p>
            </section>
            <MonitoringPanel
              status={monitoring.status}
              error={monitoring.error}
              connected={monitoring.connected}
              connectionStatus={monitoring.connectionStatus}
              onReconnect={monitoring.reconnectWebSocket}
              onStart={monitoring.startMonitoring}
              onStop={monitoring.stopMonitoring}
              canManage={role === "OPERATOR"}
            />
            <div className="card">
              <div className="card-title">MANUAL YOLO TEST</div>
              <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: "1rem" }}>
                Upload an image manually to test YOLO inference. This is independent of autonomous monitoring.
              </div>
              {role === "OPERATOR" && zones.length > 0 && <CVPanel zoneId={selectedZoneId ?? zones[0].zone_id} />}
            </div>
          </div>
        )}

        {/* ── ENERGY ───────────────────────────────────────────── */}
        {activeSection === "Energy" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <div>
              <div className="card-title">LIVE / RUNTIME ENERGY STREAM</div>
              <EnergyChart history={energyHistory} demoMode={Boolean(demoSummary?.scenario_id)} />
            </div>
            <HistoricalTelemetry buildingId={selectedBuildingId} zones={zones} />
          </div>
        )}

        {/* ── EVENTS ───────────────────────────────────────────── */}
        {activeSection === "Events" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <EventStream events={monitoring.events} connectionStatus={monitoring.connectionStatus} />
            {selectedZoneId && (
              <div style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                Zone-specific history: select a zone from the Zones tab.
              </div>
            )}
          </div>
        )}
        {activeSection === "Knowledge" && <KnowledgePanel key={selectedBuildingId ?? "no-building"} buildingId={selectedBuildingId} canManage={role === "OPERATOR"} />}
        {activeSection === "Access" && <AccessPanel role={role} />}
        {activeSection === "Integrations" && role === "OPERATOR" && <>
          <BuildingOnboarding buildingId={selectedBuildingId} onSelectBuilding={setSelectedBuildingId} onBuildingsChanged={refreshBuildings} />
          <IntegrationConfiguration buildingId={selectedBuildingId} zones={zones} />
        </>}
        {activeSection === "Audit" && role === "ADMIN" && <AuditPanel />}
      </main>
    </div>
  );
}

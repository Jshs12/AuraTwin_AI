import { useCallback, useEffect, useState } from "react";
import { useZones } from "../hooks/useZones";
import { useMonitoring } from "../hooks/useMonitoring";
import { TopNav } from "../components/layout/TopNav";
import { ZoneList } from "../components/zones/ZoneList";
import { ZoneDetail } from "../components/zones/ZoneDetail";
import { CVPanel } from "../components/occupancy/CVPanel";
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
import type { OptimizationInterval } from "../services/api";
import { ErrorState } from "../components/common";

type Section = "Overview" | "Zones" | "Occupancy" | "Energy" | "Knowledge" | "Events" | "Integrations" | "Access" | "Audit";

export function Dashboard({ role }: { role: "ADMIN" | "OPERATOR" }) {
  const [activeSection, setActiveSection] = useState<Section>("Overview");
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [demoSummary, setDemoSummary] = useState<DemoBuildingSummary | null>(null);
  const [buildings, setBuildings] = useState<Array<{ building_id: string; building_key: string; organization_id: string; name: string }>>([]);
  const [selectedBuildingId, setSelectedBuildingId] = useState<string | undefined>();
  const [buildingsError, setBuildingsError] = useState("");
  const [buildingsLoading, setBuildingsLoading] = useState(true);
  const [activeIntervals, setActiveIntervals] = useState<OptimizationInterval[]>([]);
  const [completedIntervals, setCompletedIntervals] = useState<OptimizationInterval[]>([]);

  const refreshBuildings = useCallback(async () => {
    setBuildingsLoading(true);
    setBuildingsError("");
    try {
      const items = await api.getBuildings();
      setBuildings(items);
      setSelectedBuildingId(current => current && items.some(item => item.building_id === current)
        ? current : items[0]?.building_id);
    } finally { setBuildingsLoading(false); }
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
  const { zones, loading: zonesLoading, error: zonesError, refresh: refreshZones } = useZones(selectedBuildingId);
  useEffect(() => {
    const nextSelection = defaultZoneSelection(zones, selectedZoneId);
    if (nextSelection !== selectedZoneId) setSelectedZoneId(nextSelection);
  }, [zones, selectedZoneId]);
  useEffect(() => {
    let active = true;
    const refreshIntervals = async () => {
      const results = await Promise.all(zones.map(zone => api.getOptimizationIntervals(zone.zone_id).catch(() => null)));
      if (active) {
        setActiveIntervals(results.flatMap(result => result?.active ? [result.active] : []));
        setCompletedIntervals(results.flatMap(result => result?.completed ?? []).sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at)));
      }
    };
    if (zones.length) void refreshIntervals(); else setActiveIntervals([]);
    const timer = window.setInterval(() => { if (zones.length) void refreshIntervals(); }, 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, [zones]);
  const updateDemoSummary = useCallback((summary: DemoBuildingSummary) => setDemoSummary(summary), []);
  const energyHistory = demoSummary?.scenario_id
    ? demoSummary.energy_history.map(sample => ({
        timestamp: sample.timestamp,
        time: formatISTTimestamp(sample.timestamp),
        power_kw: sample.power_kw, energy_kwh: sample.energy_kwh, occupancy: sample.occupancy, zone_id: "building",
      }))
    : monitoring.energyHistory;
  const displayPower = demoSummary?.scenario_id ? demoSummary.simulated_power_kw
    : energyHistory.length ? energyHistory.at(-1)!.power_kw : null;
  const occupancySnapshots = monitoring.status?.zones.filter(item => item.last_snapshot) ?? [];
  const currentOccupancy = occupancySnapshots.length
    ? occupancySnapshots.reduce((sum, item) => sum + item.last_people_count, 0) : null;

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
        systemOnline={Boolean(monitoring.status) && !monitoring.error}
      />

      <main className="main-content">
        {buildingsError && <ErrorState title="Building information is unavailable" onRetry={() => void refreshBuildings()} details={<code>{buildingsError}</code>}>
          Your authorized building list could not be loaded.
        </ErrorState>}
        {buildingsLoading && <div className="overview-skeleton" aria-label="Loading building overview"><span /><span /><span /><span /></div>}
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
              activeIntervals={activeIntervals}
              latestCompleted={completedIntervals[0] ?? null}
            />
            <EnergyChart
              history={energyHistory}
              demoMode={Boolean(demoSummary?.scenario_id)}
            />
            {role === "OPERATOR" && <DemoModePanel onSummary={updateDemoSummary} />}
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
          </div>
        )}

        {/* ── ZONES ────────────────────────────────────────────── */}
        {activeSection === "Zones" && (
          <div className="zones-workspace">
            <div>
              {zonesLoading ? (
                <><header className="section-header"><div><p className="eyebrow">BUILDING OPERATIONS</p><h1>Zones</h1><p>Current occupancy, comfort, HVAC and optimization state.</p></div></header><div className="zone-grid" aria-label="Loading zones">{[1,2,3,4,5,6].map(item => <div className="zone-detail-skeleton" key={item}><span /></div>)}</div></>
              ) : zonesError ? (
                <ErrorState title="Zone data is unavailable" onRetry={refreshZones} details={<code>{zonesError}</code>}>
                  The configured zones for this building could not be loaded.
                </ErrorState>
              ) : (
                <section className="zone-list-section">
                  <header className="section-header"><div><p className="eyebrow">BUILDING OPERATIONS</p><h1>Zones</h1><p>Current occupancy, comfort, HVAC and optimization state.</p></div><span className="badge neutral">{zones.length} CONFIGURED</span></header>
                  <ZoneList
                    zones={zones}
                    selectedZoneId={selectedZoneId}
                    onSelectZone={setSelectedZoneId}
                    monitoringZones={monitoring.status?.zones ?? []}
                    optimizationIntervals={activeIntervals}
                  />
                </section>
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
                <div className="occupancy-total"><strong>{currentOccupancy ?? "—"}</strong><span>{currentOccupancy === null ? "No current occupancy snapshot" : "people in monitored zones"}</span><small>{occupancySnapshots.filter(item => item.last_people_count > 0).length} occupied · {monitoring.status?.zones_enabled ?? 0} monitored</small></div>
                <div className="occupancy-zone-breakdown">{(monitoring.status?.zones ?? []).map(item => {
                  const zone = zones.find(candidate => candidate.zone_id === item.zone_id);
                  const count = item.last_snapshot ? item.last_people_count : null;
                  const capacity = zone?.capacity ?? 0;
                  const percent = capacity > 0 && count !== null ? Math.min(100, count / capacity * 100) : 0;
                  return <div key={item.zone_id} className="occupancy-bar-row"><span>{zone?.name ?? item.zone_id}</span><div className="bar-track"><i style={{ width: `${percent}%` }} /></div><b>{count ?? "—"}<small> / {capacity || "—"}</small></b></div>;
                })}{!occupancySnapshots.length && <p className="muted">Start monitoring to display current occupancy snapshots.</p>}</div>
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

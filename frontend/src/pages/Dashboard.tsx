import { useCallback, useState } from "react";
import { useZones } from "../hooks/useZones";
import { useMonitoring } from "../hooks/useMonitoring";
import { TopNav } from "../components/layout/TopNav";
import { ZoneList } from "../components/zones/ZoneList";
import { ZoneDetail } from "../components/zones/ZoneDetail";
import { CVPanel } from "../components/occupancy/CVPanel";
import { Summary } from "../components/dashboard/Summary";
import { MonitoringPanel } from "../components/monitoring/MonitoringPanel";
import { EnergyChart } from "../components/energy/EnergyChart";
import { EventStream } from "../components/events/EventStream";
import { DemoModePanel } from "../components/demo/DemoModePanel";
import type { DemoBuildingSummary } from "../types/api";
import { formatISTTimestamp } from "../utils/time";
import { AccessPanel } from "../components/security/AccessPanel";
import { AuditPanel } from "../components/security/AuditPanel";

type Section = "Overview" | "Zones" | "Occupancy" | "Energy" | "Events" | "Access" | "Audit";

export function Dashboard({ role }: { role: "ADMIN" | "OPERATOR" }) {
  const [activeSection, setActiveSection] = useState<Section>("Overview");
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [demoSummary, setDemoSummary] = useState<DemoBuildingSummary | null>(null);

  // Centralized state from WebSocket + monitoring API
  const monitoring = useMonitoring();

  // Zone list (static zones.json)
  const { zones, loading: zonesLoading, error: zonesError } = useZones();
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
        {/* ── OVERVIEW ─────────────────────────────────────────── */}
        {activeSection === "Overview" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            {role === "OPERATOR" && <DemoModePanel onSummary={updateDemoSummary} />}
            <Summary
              zones={zones}
              totalOccupancy={monitoring.totalOccupancy}
              occupiedZones={monitoring.occupiedZones}
              monitoringStatus={monitoring.status}
              currentPower={displayPower}
            />
            <MonitoringPanel
              status={monitoring.status}
              error={monitoring.error}
              connected={monitoring.connected}
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
          <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: "1.25rem" }}>
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
                <div className="card" style={{ color: "var(--text-muted)", textAlign: "center", padding: "3rem" }}>
                  Select a zone to view details
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── OCCUPANCY ────────────────────────────────────────── */}
        {activeSection === "Occupancy" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <MonitoringPanel
              status={monitoring.status}
              error={monitoring.error}
              connected={monitoring.connected}
              onStart={monitoring.startMonitoring}
              onStop={monitoring.stopMonitoring}
              canManage={role === "OPERATOR"}
            />
            <div className="card">
              <div className="card-title">MANUAL YOLO TEST</div>
              <div style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: "1rem" }}>
                Upload an image manually to test YOLO inference. This is independent of autonomous monitoring.
              </div>
              {role === "OPERATOR" && <CVPanel zoneId="classroom_01" />}
            </div>
          </div>
        )}

        {/* ── ENERGY ───────────────────────────────────────────── */}
        {activeSection === "Energy" && (
          <EnergyChart
            history={energyHistory}
            demoMode={Boolean(demoSummary?.scenario_id)}
          />
        )}

        {/* ── EVENTS ───────────────────────────────────────────── */}
        {activeSection === "Events" && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
            <EventStream events={monitoring.events} />
            {selectedZoneId && (
              <div style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
                Zone-specific history: select a zone from the Zones tab.
              </div>
            )}
          </div>
        )}
        {activeSection === "Access" && <AccessPanel role={role} />}
        {activeSection === "Audit" && role === "ADMIN" && <AuditPanel />}
      </main>
    </div>
  );
}

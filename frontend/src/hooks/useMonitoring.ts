import { useState, useEffect, useRef, useCallback } from "react";
import type { MonitoringStatus, SystemEvent } from "../types/api";
import { api } from "../services/api";
import { formatISTTimestamp } from "../utils/time";

const WS_BASE = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000`;

export interface EnergyDataPoint {
  time: string;
  timestamp: string;
  power_kw: number;
  energy_kwh?: number;
  occupancy?: number;
  zone_id?: string;
}

interface MonitoringState {
  status: MonitoringStatus | null;
  error: string | null;
  events: SystemEvent[];
  energyHistory: EnergyDataPoint[];
  connected: boolean;
  currentZoneIndex: number;
}

const MAX_EVENTS = 100;
const MAX_ENERGY_POINTS = 120;

export function useMonitoring() {
  const [state, setState] = useState<MonitoringState>({
    status: null,
    error: null,
    events: [],
    energyHistory: [],
    connected: false,
    currentZoneIndex: 0,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const fetchStatus = useCallback(async () => {
    try {
      const [s, demoEvents, demoSummary] = await Promise.all([api.getMonitoringStatus(), api.getDemoEvents(), api.getDemoBuildingSummary()]);
      if (mountedRef.current) setState(prev => {
        const known = new Set(demoEvents.map(event => event.event_id));
        const justResetDemo = Boolean(prev.status?.demo_simulation && !s.demo_simulation && demoEvents.length === 0);
        const events = demoEvents.length
          ? [...demoEvents].reverse().concat(prev.events.filter(event => !known.has(event.event_id))).slice(0, MAX_EVENTS)
          : justResetDemo ? prev.events.filter(event => !event.payload.scenario_id) : prev.events;
        const energyHistory = demoSummary.scenario_id
          ? demoSummary.energy_history.map(sample => ({
              timestamp: sample.timestamp,
              time: formatISTTimestamp(sample.timestamp),
              power_kw: sample.power_kw, energy_kwh: sample.energy_kwh, occupancy: sample.occupancy, zone_id: "building",
            })).slice(-MAX_ENERGY_POINTS)
          : justResetDemo ? [] : prev.energyHistory.filter(point => point.zone_id !== "building");
        return { ...prev, status: s, error: null, events, energyHistory };
      });
    } catch {
      if (mountedRef.current) setState(prev => ({ ...prev, error: "Backend status is unavailable. Check that AuraTwin's API is running." }));
    }
  }, []);

  const connectWebSocket = useCallback(() => {
    const readyState = wsRef.current?.readyState;
    if (readyState === WebSocket.CONNECTING || readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(`${WS_BASE}/api/monitoring/ws/events`);
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      if (mountedRef.current) setState(prev => ({ ...prev, connected: true }));
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return;
      try {
        const msg: SystemEvent = JSON.parse(event.data);
        if (!mountedRef.current) return;

        setState(prev => {
          const newEvents = prev.events.some(existing => existing.event_id === msg.event_id)
            ? prev.events
            : [msg, ...prev.events].slice(0, MAX_EVENTS);

          let newEnergy = prev.energyHistory;
          if (msg.event_type === "ENERGY_UPDATE"
              && (msg.zone_id === "building" || !prev.status?.demo_simulation)) {
            const payload = msg.payload as Record<string, unknown>;
            const timestamp = typeof payload.timestamp === "string" ? payload.timestamp : msg.timestamp;
            const point: EnergyDataPoint = {
              timestamp,
              time: formatISTTimestamp(timestamp),
              power_kw: typeof payload.power_kw === "number" ? payload.power_kw : 0,
              energy_kwh: typeof payload.energy_kwh === "number" ? payload.energy_kwh : 0,
              occupancy: typeof payload.occupancy === "number" ? payload.occupancy : undefined,
              zone_id: msg.zone_id,
            };
            if (!prev.energyHistory.some(existing => existing.timestamp === point.timestamp))
              newEnergy = [...prev.energyHistory, point].slice(-MAX_ENERGY_POINTS);
          }

          return { ...prev, events: newEvents, energyHistory: newEnergy };
        });

        // Refresh status after key events
        if (["SNAPSHOT_CAPTURED", "YOLO_DETECTION", "OCCUPANCY_CHANGED"].includes(msg.event_type)) {
          fetchStatus();
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      wsRef.current = null;
      if (!mountedRef.current) return;
      setState(prev => ({ ...prev, connected: false }));
      // Reconnect after 3s
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      reconnectTimer.current = setTimeout(() => {
        if (mountedRef.current) connectWebSocket();
      }, 3000);
    };

    ws.onerror = () => {
      if (wsRef.current === ws) ws.close();
    };
  }, [fetchStatus]);

  const startMonitoring = useCallback(async () => {
    await api.startMonitoring();
    await fetchStatus();
  }, [fetchStatus]);

  const stopMonitoring = useCallback(async () => {
    await api.stopMonitoring();
    await fetchStatus();
  }, [fetchStatus]);

  // Initial fetch and ws connect
  useEffect(() => {
    mountedRef.current = true;
    fetchStatus();
    connectWebSocket();

    // Poll status every 4s as backup
    const poll = setInterval(fetchStatus, 4000);

    return () => {
      mountedRef.current = false;
      clearInterval(poll);
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close();
    };
  }, [fetchStatus, connectWebSocket]);

  // Derive aggregate occupancy from monitoring status
  const totalOccupancy = state.status?.zones.reduce((sum, z) => sum + z.last_people_count, 0) ?? 0;
  const occupiedZones = state.status?.zones.filter(z => z.last_people_count > 0).length ?? 0;

  // Current energy aggregates from recent history
  const recentEnergy = state.energyHistory.slice(-120);
  const currentPower = recentEnergy.length > 0
    ? recentEnergy[recentEnergy.length - 1].power_kw
    : 0;
  const avgPower = recentEnergy.length > 0
    ? recentEnergy.reduce((s, p) => s + p.power_kw, 0) / recentEnergy.length
    : 0;
  const peakPower = recentEnergy.length > 0
    ? Math.max(...recentEnergy.map(p => p.power_kw))
    : 0;

  return {
    status: state.status,
    error: state.error,
    events: state.events,
    energyHistory: state.energyHistory,
    connected: state.connected,
    totalOccupancy,
    occupiedZones,
    currentPower,
    avgPower,
    peakPower,
    startMonitoring,
    stopMonitoring,
    refreshStatus: fetchStatus,
  };
}

import { useState, useEffect, useRef, useCallback } from "react";
import type { MonitoringStatus, SystemEvent } from "../types/api";
import { api, authSession } from "../services/api";
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
  connectionStatus: "connecting" | "connected" | "disconnected" | "authentication-required" | "authorization-denied";
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
    connectionStatus: authSession.getToken() ? "connecting" : "authentication-required",
    currentZoneIndex: 0,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempt = useRef(0);
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
    if (connectTimer.current) clearTimeout(connectTimer.current);
    if (reconnectTimer.current) { clearTimeout(reconnectTimer.current); reconnectTimer.current = null; }
    const readyState = wsRef.current?.readyState;
    if (readyState === WebSocket.CONNECTING || readyState === WebSocket.OPEN) return;

    const token = authSession.getToken();
    if (!token) {
      if (mountedRef.current) setState(prev => ({ ...prev, connected: false, connectionStatus: "authentication-required" }));
      return;
    }
    if (mountedRef.current) setState(prev => ({ ...prev, connected: false, connectionStatus: "connecting" }));
    // StrictMode deliberately replays effects in development. Deferring socket
    // creation lets the discarded first mount cancel before a handshake begins.
    connectTimer.current = setTimeout(() => {
      if (!mountedRef.current || authSession.getToken() !== token) return;
      const ws = new WebSocket(`${WS_BASE}/api/monitoring/ws/events?access_token=${encodeURIComponent(token)}`);
      wsRef.current = ws;
      ws.onopen = () => {
        if (wsRef.current !== ws) { ws.close(1000, "component unmounted"); return; }
        reconnectAttempt.current = 0;
        if (mountedRef.current) setState(prev => ({ ...prev, connected: true, connectionStatus: "connected" }));
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
          // Ignore malformed event frames; keep the authenticated stream alive.
        }
      };

      ws.onclose = (event) => {
        if (wsRef.current !== ws) return;
        wsRef.current = null;
        if (!mountedRef.current) return;
        setState(prev => ({ ...prev, connected: false,
          connectionStatus: event.code === 4401 ? "authentication-required"
            : event.code === 4403 ? "authorization-denied" : "disconnected" }));
        if (event.code === 4401) {
          authSession.clear();
          window.dispatchEvent(new Event("auratwin:session-expired"));
          return;
        }
        if (event.code === 4403 || event.code === 1000) return;
        if (reconnectAttempt.current >= 5) return;
        reconnectAttempt.current += 1;
        const delay = Math.min(1000 * (2 ** (reconnectAttempt.current - 1)), 16000);
        reconnectTimer.current = setTimeout(() => {
          if (mountedRef.current) connectWebSocket();
        }, delay);
      };

      ws.onerror = () => {
        // onclose carries the retry/auth code and is the single reconnect path.
      };
    }, 0);
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
      if (connectTimer.current) clearTimeout(connectTimer.current);
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws?.readyState === WebSocket.OPEN) ws.close(1000, "dashboard unmounted");
      // Closing CONNECTING sockets emits a misleading browser console error.
      // Let that handshake settle and close it immediately on open instead.
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
    connectionStatus: state.connectionStatus,
    totalOccupancy,
    occupiedZones,
    currentPower,
    avgPower,
    peakPower,
    startMonitoring,
    stopMonitoring,
    refreshStatus: fetchStatus,
    reconnectWebSocket: connectWebSocket,
  };
}

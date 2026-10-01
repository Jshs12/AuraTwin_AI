import { useState, useEffect, useCallback, useRef } from "react";
import type { ZoneState, SystemEvent, RecommendationDecision, ControlResult } from "../types/api";
import { api, authSession, ApiRequestError } from "../services/api";
import type { OptimizationInterval } from "../services/api";

export function useZoneState(zoneId: string | null) {
  const [state, setState] = useState<ZoneState | null>(null);
  const [history, setHistory] = useState<SystemEvent[]>([]);
  const [recommendation, setRecommendation] = useState<RecommendationDecision | null>(null);
  const [controlResult, setControlResult] = useState<ControlResult | null>(null);
  const [optimizationIntervals, setOptimizationIntervals] = useState<{ active: OptimizationInterval | null; completed: OptimizationInterval[] } | null>(null);
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollBlockedRef = useRef(false);

  const fetchZoneData = useCallback(async () => {
    if (!zoneId) {
      setState(null);
      setHistory([]);
      setRecommendation(null);
      setControlResult(null);
      setOptimizationIntervals(null);
      return;
    }
    
    try {
      setLoading(true);
      setError(null);
      
      const stateData = await api.getZoneState(zoneId);
      if (!authSession.getToken()) return;
      const historyData = await api.getZoneHistory(zoneId);
      const intervals = await api.getOptimizationIntervals(zoneId);
      
      setState(stateData);
      setHistory(historyData);
      setRecommendation(null); // Clear previous recommendation on refresh
      setOptimizationIntervals(intervals);
    } catch (err: any) {
      setError(err.message || "Failed to load zone state");
    } finally {
      setLoading(false);
    }
  }, [zoneId]);

  useEffect(() => {
    fetchZoneData();
  }, [fetchZoneData]);

  useEffect(() => {
    if (!zoneId) return;
    pollBlockedRef.current = false;
    const poll = window.setInterval(() => {
      if (!authSession.getToken() || pollBlockedRef.current) return;
      Promise.all([api.getZoneState(zoneId), api.getOptimizationIntervals(zoneId)]).then(([data, intervals]) => { setState(data); setOptimizationIntervals(intervals); setError(null); }).catch(err => {
        if (err instanceof ApiRequestError && (err.status === 401 || err.status === 403)) pollBlockedRef.current = true;
        setError(err instanceof Error ? err.message : "Unable to refresh zone state.");
      });
    }, 2000);
    return () => window.clearInterval(poll);
  }, [zoneId]);

  const generateRec = async () => {
    if (!zoneId) return;
    try {
      setLoading(true);
      const rec = await api.generateRecommendation(zoneId);
      setRecommendation(rec);
      // Refresh history to show the event
      const historyData = await api.getZoneHistory(zoneId);
      setHistory(historyData);
    } catch (err: any) {
      setError(err instanceof ApiRequestError && err.code === "OPTIMIZATION_HOLDING"
        ? "The validated setpoint is holding. A new recommendation will be evaluated after occupancy changes."
        : err.message || "Failed to generate recommendation");
    } finally {
      setLoading(false);
    }
  };

  const applyRec = async () => {
    if (!zoneId || !recommendation) return;
    try {
      setLoading(true);
      const result = await api.applyControl(zoneId, recommendation);
      setControlResult(result);
      if (result.success) {
        // Refresh everything
        await fetchZoneData();
      } else {
        setError(result.error_message || `Control ${result.status.toLowerCase()}`);
      }
    } catch (err: any) {
      setError(err instanceof ApiRequestError && err.code === "OPTIMIZATION_HOLDING"
        ? "The active optimization is holding its validated setpoint until occupancy changes."
        : err.message || "Failed to apply control");
    } finally {
      setLoading(false);
    }
  };

  return {
    state,
    history,
    recommendation,
    controlResult,
    optimizationIntervals,
    loading,
    error,
    refresh: fetchZoneData,
    generateRecommendation: generateRec,
    applyRecommendation: applyRec
  };
}

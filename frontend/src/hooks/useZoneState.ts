import { useState, useEffect, useCallback } from "react";
import type { ZoneState, SystemEvent, RecommendationDecision, ControlResult } from "../types/api";
import { api } from "../services/api";

export function useZoneState(zoneId: string | null) {
  const [state, setState] = useState<ZoneState | null>(null);
  const [history, setHistory] = useState<SystemEvent[]>([]);
  const [recommendation, setRecommendation] = useState<RecommendationDecision | null>(null);
  const [controlResult, setControlResult] = useState<ControlResult | null>(null);
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchZoneData = useCallback(async () => {
    if (!zoneId) {
      setState(null);
      setHistory([]);
      setRecommendation(null);
      setControlResult(null);
      return;
    }
    
    try {
      setLoading(true);
      setError(null);
      
      const [stateData, historyData] = await Promise.all([
        api.getZoneState(zoneId),
        api.getZoneHistory(zoneId).catch(() => []) // Optional fallback
      ]);
      
      setState(stateData);
      setHistory(historyData);
      setRecommendation(null); // Clear previous recommendation on refresh
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
    const poll = window.setInterval(() => {
      api.getZoneState(zoneId).then(setState).catch(() => undefined);
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
      setError(err.message || "Failed to generate recommendation");
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
      setError(err.message || "Failed to apply control");
    } finally {
      setLoading(false);
    }
  };

  return {
    state,
    history,
    recommendation,
    controlResult,
    loading,
    error,
    refresh: fetchZoneData,
    generateRecommendation: generateRec,
    applyRecommendation: applyRec
  };
}

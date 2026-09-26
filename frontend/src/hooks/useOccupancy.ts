import { useState, useEffect } from "react";
import type { OccupancyStatus, OccupancyDetectionResponse } from "../types/api";
import { api } from "../services/api";

export function useOccupancy() {
  const [status, setStatus] = useState<OccupancyStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detectionResult, setDetectionResult] = useState<OccupancyDetectionResponse | null>(null);

  useEffect(() => {
    async function loadStatus() {
      try {
        setLoading(true);
        const data = await api.getOccupancyStatus();
        setStatus(data);
      } catch (err: any) {
        console.error("Failed to load occupancy status", err);
      } finally {
        setLoading(false);
      }
    }
    loadStatus();
  }, []);

  const detect = async (file: File, zoneId: string) => {
    try {
      setDetecting(true);
      setError(null);
      setDetectionResult(null);
      const result = await api.detectOccupancy(file, zoneId);
      setDetectionResult(result);
      return result;
    } catch (err: any) {
      setError(err.message || "Failed to run detection");
      return null;
    } finally {
      setDetecting(false);
    }
  };

  return { status, detectionResult, detect, loading, detecting, error };
}

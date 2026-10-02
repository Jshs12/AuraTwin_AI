import { useState, useEffect, useCallback } from "react";
import type { Zone } from "../types/api";
import { api } from "../services/api";

export function useZones(buildingId?: string) {
  const [zones, setZones] = useState<Zone[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    async function loadZones() {
      try {
        setLoading(true);
        const data = await api.getZones(buildingId);
        setZones(data);
        setError(null);
      } catch (err: any) {
        setError(err.message || "Failed to load zones");
      } finally {
        setLoading(false);
      }
    }
    loadZones();
  }, [buildingId, refreshToken]);

  const refresh = useCallback(() => setRefreshToken(value => value + 1), []);
  return { zones, loading, error, refresh };
}

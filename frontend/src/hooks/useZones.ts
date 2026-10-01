import { useState, useEffect } from "react";
import type { Zone } from "../types/api";
import { api } from "../services/api";

export function useZones(buildingId?: string) {
  const [zones, setZones] = useState<Zone[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
  }, [buildingId]);

  return { zones, loading, error };
}

import { useState, useEffect } from "react";
import type { Zone } from "../types/api";
import { api } from "../services/api";

export function useZones() {
  const [zones, setZones] = useState<Zone[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadZones() {
      try {
        setLoading(true);
        const data = await api.getZones();
        setZones(data);
        setError(null);
      } catch (err: any) {
        setError(err.message || "Failed to load zones");
      } finally {
        setLoading(false);
      }
    }
    loadZones();
  }, []);

  return { zones, loading, error };
}

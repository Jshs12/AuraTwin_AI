import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../services/api";
import type { Floor, Zone } from "../../types/api";

type Org = { organization_id: string; name: string; slug: string };
type Building = { building_id: string; organization_id: string; name: string; slug: string };

/** Operator setup for the existing persisted organization/building hierarchy. */
export function BuildingOnboarding({ buildingId, onSelectBuilding, onBuildingsChanged }: {
  buildingId?: string; onSelectBuilding: (id: string) => void; onBuildingsChanged: () => Promise<void>;
}) {
  const [organizations, setOrganizations] = useState<Org[]>([]);
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [floors, setFloors] = useState<Floor[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [floorId, setFloorId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = async () => {
    const [orgs, places] = await Promise.all([api.getOrganizations(), api.getBuildings()]);
    setOrganizations(orgs); setBuildings(places as Building[]);
  };
  useEffect(() => { void refresh().catch(e => setError(e.message)); }, []);
  useEffect(() => {
    let active = true;
    setFloors([]); setZones([]); setFloorId("");
    if (!buildingId) return () => { active = false; };
    Promise.all([api.getFloors(buildingId), api.getZones(buildingId)]).then(([nextFloors, nextZones]) => {
      if (!active) return;
      setFloors(nextFloors); setZones(nextZones); setFloorId(nextFloors[0]?.floor_id ?? "");
    }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [buildingId]);

  const submit = async (event: FormEvent<HTMLFormElement>, action: (data: FormData) => Promise<void>) => {
    event.preventDefault(); setError(""); setNotice("");
    try { await action(new FormData(event.currentTarget)); event.currentTarget.reset(); setNotice("Saved. Building structure refreshed."); }
    catch (e) { setError(e instanceof Error ? e.message : "Configuration request failed"); }
  };
  const refreshStructure = async () => {
    await refresh(); await onBuildingsChanged();
    if (buildingId) {
      const [nextFloors, nextZones] = await Promise.all([api.getFloors(buildingId), api.getZones(buildingId)]);
      setFloors(nextFloors); setZones(nextZones); setFloorId(nextFloors[0]?.floor_id ?? "");
    }
  };

  return <section className="card" aria-label="Building onboarding">
    <div className="card-title">BUILDING SETUP · OPERATOR ONBOARDING</div>
    <p className="muted">Organizations are provisioned by the account administrator. This screen configures authorized buildings, floors, and zones.</p>
    <div className="section-block">
      <strong>Organization → Building → Floor → Zone</strong>
      <div className="muted" style={{ marginTop: ".35rem" }}>Organization: {organizations.map(item => item.name).join(", ") || "No authorized organization available"}</div>
      <form className="historical-filters" onSubmit={event => void submit(event, async data => {
        const item = await api.createBuilding({ organization_id: String(data.get("organization_id")), name: String(data.get("name")), slug: String(data.get("slug")), timezone: String(data.get("timezone")) || "UTC" }) as Building;
        await refreshStructure(); onSelectBuilding(item.building_id);
      })}>
        <label>Organization<select name="organization_id" required>{organizations.map(org => <option key={org.organization_id} value={org.organization_id}>{org.name}</option>)}</select></label>
        <label>Building name<input name="name" required maxLength={200} /></label>
        <label>Building slug<input name="slug" required pattern="[a-z0-9][a-z0-9-]*" /></label>
        <label>Timezone<input name="timezone" defaultValue="UTC" required /></label>
        <button className="btn btn-primary" disabled={!organizations.length}>Add building</button>
      </form>
    </div>
    <div className="section-block">
      <label>Selected building<select value={buildingId ?? ""} onChange={event => onSelectBuilding(event.target.value)}>
        <option value="">Select building</option>{buildings.map(item => <option key={item.building_id} value={item.building_id}>{item.name}</option>)}
      </select></label>
      {!!buildingId && <>
        <div className="historical-metrics" style={{ marginTop: ".75rem" }}>
          <div><small>STRUCTURE</small><strong>Building ✓ · {floors.length} floor(s) · {zones.length} zone(s)</strong></div>
          <div><small>HARDWARE</small><strong>NOT CONNECTED · CONFIGURATION ONLY</strong></div>
          <div><small>CONTROL</small><strong>Safety policy remains enforced by backend</strong></div>
        </div>
        <form className="historical-filters" onSubmit={event => void submit(event, async data => {
          await api.createFloor(buildingId, { name: String(data.get("name")), floor_key: String(data.get("floor_key")), level_number: Number(data.get("level_number")) }); await refreshStructure();
        })}>
          <label>Floor name<input name="name" required /></label><label>Floor key<input name="floor_key" pattern="[a-z0-9][a-z0-9-]*" required /></label><label>Level<input name="level_number" type="number" defaultValue="0" /></label><button className="btn btn-secondary">Add floor</button>
        </form>
        <form className="historical-filters" onSubmit={event => void submit(event, async data => {
          await api.createZone(floorId, { zone_key: String(data.get("zone_key")), name: String(data.get("zone_name")), type: String(data.get("zone_type")), capacity: Number(data.get("capacity")), area_m2: Number(data.get("area_m2")), comfort: { min_temperature: Number(data.get("comfort_min")), max_temperature: Number(data.get("comfort_max")) } }); await refreshStructure();
        })}>
          <label>Floor<select value={floorId} onChange={event => setFloorId(event.target.value)} required>{floors.map(floor => <option key={floor.floor_id} value={floor.floor_id}>{floor.name}</option>)}</select></label>
          <label>Zone name<input name="zone_name" required /></label><label>Zone key<input name="zone_key" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" required /></label><label>Type<input name="zone_type" defaultValue="classroom" required /></label>
          <label>Capacity<input name="capacity" type="number" min="0" defaultValue="20" required /></label><label>Area m²<input name="area_m2" type="number" min="0" step="any" defaultValue="30" required /></label><label>Comfort min °C<input name="comfort_min" type="number" step="any" defaultValue="21" required /></label><label>Comfort max °C<input name="comfort_max" type="number" step="any" defaultValue="25" required /></label><button className="btn btn-secondary" disabled={!floors.length}>Add zone</button>
        </form>
        {zones.length > 0 && <ul>{zones.map(zone => <li key={zone.zone_id}>{zone.name} · floor {floors.find(f => f.floor_id === zone.floor_id)?.name ?? "configured"} · {zone.capacity} capacity</li>)}</ul>}
      </>}
    </div>
    {error && <div className="demo-error" role="alert">{error}</div>}{notice && <div role="status">{notice}</div>}
  </section>;
}

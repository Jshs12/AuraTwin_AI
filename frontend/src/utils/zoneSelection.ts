export function defaultZoneSelection<T extends { zone_id: string }>(zones: T[], selectedZoneId: string | null): string | null {
  if (!zones.length) return null;
  return selectedZoneId && zones.some(zone => zone.zone_id === selectedZoneId)
    ? selectedZoneId
    : zones[0].zone_id;
}

// The nearest radar antenna to a point, by great-circle distance — the same
// pick the legacy controller resolves from the map centre. Distance is
// geometry, not meteorology, so it lives in the frontend.

/**
 * The id of the catalogue antenna closest to `latitude`/`longitude`, or null
 * when the catalogue is empty. Great-circle distance, so site choice stays
 * right at high latitude: one degree of longitude is 111 km at the equator
 * and 57 km at 60 N, and comparing raw coordinate differences would pick the
 * wrong Alaskan radar.
 */
export function nearestRadarSite(
  sites: readonly { id: string; latitude: number; longitude: number }[],
  latitude: number,
  longitude: number,
): string | null {
  const radius = 6371.0;
  const phi = (Math.PI * latitude) / 180;
  let best: { id: string; km: number } | null = null;
  for (const site of sites) {
    const other = (Math.PI * site.latitude) / 180;
    const dPhi = other - phi;
    const dLambda = (Math.PI * (site.longitude - longitude)) / 180;
    const a = Math.sin(dPhi / 2) ** 2 + Math.cos(phi) * Math.cos(other) * Math.sin(dLambda / 2) ** 2;
    const km = 2 * radius * Math.asin(Math.min(1, Math.sqrt(a)));
    if (best === null || km < best.km) {
      best = { id: site.id, km };
    }
  }
  return best?.id ?? null;
}

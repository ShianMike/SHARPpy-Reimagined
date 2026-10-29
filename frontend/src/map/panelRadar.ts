import type { WeatherOptions } from "../stores/mapStore";
import { RADAR_TILTABLE_PRODUCTS, type RadarSite } from "../types/map";

export function isAustralianRadarSite(site: string | null): boolean {
  return site !== null && /^A[A-Z0-9]{3}$/.test(site);
}

/** Resolve the same capabilities for controls, displayed imagery and playback. */
export function panelRadar(
  weather: Pick<WeatherOptions, "radarScope" | "radarSite" | "radarProduct" | "radarTilt">,
  nearestSite: string | null,
  sites: readonly RadarSite[] = [],
) {
  const site = weather.radarSite ?? nearestSite;
  const metadata = sites.find((item) => item.id === site);
  const australian = metadata?.country === "AU" || isAustralianRadarSite(site);
  // A pinned Australian site can render before the catalogue has arrived.
  const supportedProducts = metadata?.products ?? (australian ? ["reflectivity"] : undefined);
  const requested = weather.radarProduct ?? "reflectivity";
  const product = weather.radarScope === "mosaic" || supportedProducts === undefined ? weather.radarProduct
    : supportedProducts.includes(requested) ? requested : supportedProducts[0] ?? null;
  return {
    site,
    supportedProducts,
    product,
    tilt: weather.radarScope === "site" && australian ? "0" as const : weather.radarTilt,
    tiltable: weather.radarScope === "site" && !australian && RADAR_TILTABLE_PRODUCTS.some((item) => item === (product ?? "reflectivity")),
  };
}

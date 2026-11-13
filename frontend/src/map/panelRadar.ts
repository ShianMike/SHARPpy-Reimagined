import type { WeatherOptions } from "../stores/mapStore";
import { RADAR_TILTABLE_PRODUCTS, type RadarSite } from "../types/map";

export function isFixedScanRadarSite(site: string | null, metadata?: RadarSite): boolean {
  return metadata?.provider === "MRRL" || metadata?.provider === "OPERA" || metadata?.country === "AU" || metadata?.country === "DE"
    || (site !== null && /^(?:A[A-Z0-9]{3}|D[A-Z]{3}|MF\d{2}|0-\d{1,3}-0-[a-z0-9]{5})$/i.test(site));
}

export function radarNetwork(site: RadarSite): string {
  return site.provider === "OPERA" ? "OPERA" : site.provider === "MRRL" ? site.country === undefined ? "MRRL" : `MRRL:${site.country}` : site.country ?? "US";
}

export function radarNetworkLabel(country: string): string {
  return country === "OPERA" ? "Europe · OPERA" : country === "DE" ? "Germany · DWD" : country === "AU" ? "Australia · MRRL" : country === "US" ? "United States · NEXRAD"
    : country.startsWith("MRRL:") ? `${regions.of(country.slice(5)) ?? country.slice(5)} · MRRL` : country;
}

const regions = new Intl.DisplayNames(["en"], { type: "region" });

export function radarSiteCode(id: string): string {
  return /^0-\d{1,3}-0-([a-z0-9]{5})$/i.exec(id)?.[1]?.toUpperCase() ?? id;
}

export function radarSiteCountry(site: RadarSite): string | undefined {
  return site.country === undefined ? undefined : regions.of(site.country);
}

export function radarSiteOption(site: RadarSite) {
  const code = radarSiteCode(site.id);
  const network = site.provider === "OPERA" ? `${radarSiteCountry(site) ?? "Europe"} · OPERA` : radarNetworkLabel(radarNetwork(site));
  return {
    value: site.id,
    label: code,
    detail: [site.name ?? "", code === site.id ? "" : site.id, network].filter(Boolean).join(" · "),
  };
}

/** Resolve the same capabilities for controls, displayed imagery and playback. */
export function panelRadar(
  weather: Pick<WeatherOptions, "radarScope" | "radarSite" | "radarProduct" | "radarTilt">,
  nearestSite: string | null,
  sites: readonly RadarSite[] = [],
) {
  const site = weather.radarSite ?? nearestSite;
  const metadata = sites.find((item) => item.id === site);
  const fixedScan = isFixedScanRadarSite(site, metadata);
  // A pinned native site can render before the catalogue has arrived.
  const supportedProducts = metadata?.products ?? (fixedScan ? /^A[A-Z0-9]{3}$/i.test(site ?? "") ? ["reflectivity", "velocity"] : ["reflectivity"] : undefined);
  const requested = weather.radarProduct ?? "reflectivity";
  const product = weather.radarScope === "mosaic" || supportedProducts === undefined ? weather.radarProduct
    : supportedProducts.includes(requested) ? requested : supportedProducts[0] ?? null;
  return {
    site,
    siteCode: site === null ? null : radarSiteCode(site),
    provider: metadata?.provider,
    supportedProducts,
    product,
    tilt: weather.radarScope === "site" && fixedScan ? "0" as const : weather.radarTilt,
    tiltable: weather.radarScope === "site" && !fixedScan && RADAR_TILTABLE_PRODUCTS.some((item) => item === (product ?? "reflectivity")),
  };
}

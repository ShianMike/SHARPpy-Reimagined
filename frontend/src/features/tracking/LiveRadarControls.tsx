import { Antenna, Globe, Layers, Radar } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useMapCatalog, usePanelRadar, useRadarSiteFrame, useRadarSites } from "../../map/mapQueries";
import { nearestRadarSite } from "../../map/nearestSite";
import { radarNetwork, radarNetworkLabel, radarSiteCode, radarSiteOption } from "../../map/panelRadar";
import { panelWeather, useMapStore } from "../../stores/mapStore";
import { Surface } from "../../ui/Surface";
import { RadarTiltSelect } from "./RadarTiltSelect";
import { useWeatherTime } from "./useWeatherTime";
import { SourceSelect } from "../sounding/SourceFields";
import controls from "../toolbar/ForecastControls.module.css";
import styles from "./LiveRadarControls.module.css";

export function LiveRadarControls({ className, scansRef }: { className?: string | undefined; scansRef?: ((element: HTMLDivElement | null) => void) | undefined }): ReactNode {
  const panel = useMapStore((state) => state.activePanel);
  const { radarScope: scope, radarSite: site } = useMapStore((state) => panelWeather(state));
  const catalog = useMapCatalog();
  const sites = useRadarSites(true);
  const [network, setNetwork] = useState("all");
  const entries = sites.data?.sites ?? [];
  const selectedSite = entries.find((item) => item.id === site);
  const activeNetwork = selectedSite !== undefined && network !== "all" && radarNetwork(selectedSite) !== network ? "all" : network;
  const networks = [...new Set(entries.map(radarNetwork))];
  const { site: effectiveSite, siteCode, product, tilt, tiltable, supportedProducts, provider } = usePanelRadar();
  const products = ((scope === "site" ? catalog.data?.siteProducts : catalog.data?.radarProducts) ?? [])
    .filter((item) => scope !== "site" || supportedProducts === undefined || supportedProducts.includes(item.key));
  const selected = products.find((item) => item.key === product) ?? products[0];
  const scan = useRadarSiteFrame(product, effectiveSite, false, useWeatherTime(), tilt).data;
  const german = entries.find((item) => item.id === effectiveSite)?.country === "DE" || /^D[A-Z]{3}$/.test(effectiveSite ?? "");
  const tiltLabel = scope === "site" && german ? "Variable"
    : scope === "mosaic" && selected?.key === "composite-reflectivity" ? "All tilts"
    : selected?.key === "echo-tops" ? "Full volume"
    : selected?.key === "reflectivity" || selected?.key === "base-reflectivity" || selected?.key === "velocity" || selected?.key === "hydrometeor" ? "Lowest available" : "Not applicable";
  return <Surface className={cx(controls.fields, styles.fields, className)} data-motion="fade" role="group" aria-label="Live radar" aria-description={`Controls for Panel ${String(panel + 1)}. Available elevations depend on the radar and its scan pattern.`} data-testid="live-radar-fields">
    <SourceSelect label="Network" toolbarIcon={Globe} popup="list" value={activeNetwork}
      options={[{ value: "all", label: "All networks" }, ...networks.map((country) => ({ value: country, label: radarNetworkLabel(country).split(" · ")[0] ?? country, detail: country === "OPERA" ? "Europe · OPERA · compatible scans only" : radarNetworkLabel(country) }))]}
      onChange={(country) => {
        setNetwork(country);
        if (country === "all") return;
        const state = useMapStore.getState();
        const center = state.cameraSnapshot?.center ?? [0, 0];
        const id = nearestRadarSite(entries.filter((item) => radarNetwork(item) === country), center[1], center[0]);
        const target = entries.find((item) => item.id === id);
        if (target !== undefined) {
          state.setRadarScope("site"); state.setRadarSite(target.id);
          state.requestFocus(target.id, { latitude: target.latitude, longitude: target.longitude });
        }
      }} />
    <SourceSelect label="Site" toolbarIcon={Antenna} popup="list" searchLabel="Find a radar by code or place name"
      value={scope === "mosaic" ? "mosaic" : site ?? "nearest"}
      options={[
        ...(activeNetwork === "all" || activeNetwork === "US" ? [{ value: "mosaic", label: "CONUS mosaic" }] : []),
        ...(activeNetwork === "all" ? [{ value: "nearest", label: "Nearest site", detail: scope === "site" && site === null ? siteCode ?? undefined : undefined }] : []),
        ...(scope === "site" && site !== null && selectedSite === undefined ? [{ value: site, label: radarSiteCode(site) }] : []),
        ...entries.filter((item) => activeNetwork === "all" || radarNetwork(item) === activeNetwork)
          .map(radarSiteOption).sort((a, b) => a.label.localeCompare(b.label) || a.value.localeCompare(b.value)),
      ]}
      onChange={(value) => {
        const state = useMapStore.getState();
        const next = value === "mosaic" ? "mosaic" : "site";
        if (panelWeather(state).radarScope !== next) state.setRadarScope(next);
        state.setRadarSite(value === "nearest" || value === "mosaic" ? null : value);
        const selectedSite = sites.data?.sites.find((item) => item.id === value);
        if (selectedSite !== undefined) state.requestFocus(selectedSite.id, { latitude: selectedSite.latitude, longitude: selectedSite.longitude });
      }} />
    <SourceSelect label={`Site Product${panel > 0 ? ` · ${String(panel + 1)}` : ""}`} toolbarIcon={Radar} popup="list" value={selected?.key ?? ""} disabled={products.length === 0}
      options={products.length === 0 ? [{ value: "", label: catalog.isError ? "Unavailable" : "Loading…" }] : products.map((item) => ({ value: item.key, label: provider === "MRRL" && item.key === "reflectivity" ? "Reflectivity" : item.label, detail: item.units }))}
      onChange={useMapStore.getState().setRadarProduct} />
    <div ref={scansRef} className={styles.scanSlot} />
    {tiltable && selected !== undefined ? <RadarTiltSelect label="Site Tilt" toolbarIcon={Layers} site={effectiveSite} product={selected.key} tilt={tilt} angle={scan?.elevationAngle} />
      : <button type="button" className={controls.trigger} aria-label="Site Tilt" aria-description={german && scope === "site" ? "DWD precipitation scans vary their elevation with direction." : scope === "site" && tiltLabel === "Lowest available" ? "This radar provides its lowest available elevation." : "Choose Base Reflectivity, Velocity or Correlation Coefficient to select an elevation. Accumulations and Enhanced Echo Tops use the full volume."} disabled>
        <Layers className="icon-sm" aria-hidden="true" /><span className={controls.triggerCopy}><span className={controls.name}>Site Tilt</span><strong>{tiltLabel}</strong></span>
      </button>}
  </Surface>;
}

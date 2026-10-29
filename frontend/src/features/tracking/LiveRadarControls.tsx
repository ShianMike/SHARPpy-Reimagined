import { Antenna, Layers, Radar } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useMapCatalog, usePanelRadar, useRadarSiteFrame, useRadarSites } from "../../map/mapQueries";
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
  const { site: effectiveSite, product, tilt, tiltable, supportedProducts } = usePanelRadar();
  const products = ((scope === "site" ? catalog.data?.siteProducts : catalog.data?.radarProducts) ?? [])
    .filter((item) => scope !== "site" || supportedProducts === undefined || supportedProducts.includes(item.key));
  const selected = products.find((item) => item.key === product) ?? products[0];
  const scan = useRadarSiteFrame(product, effectiveSite, false, useWeatherTime(), tilt).data;
  const tiltLabel = scope === "mosaic" && selected?.key === "composite-reflectivity" ? "All tilts"
    : selected?.key === "echo-tops" ? "Full volume"
    : selected?.key === "reflectivity" || selected?.key === "base-reflectivity" || selected?.key === "velocity" || selected?.key === "hydrometeor" ? "Lowest available" : "Not applicable";
  return <Surface className={cx(controls.fields, className)} data-motion="fade" role="group" aria-label="Live radar" aria-description={`Controls for Panel ${String(panel + 1)}. Available elevations depend on the radar and its scan pattern.`} data-testid="live-radar-fields">
    <SourceSelect label="Site" toolbarIcon={Antenna} value={scope === "mosaic" ? "mosaic" : site ?? "nearest"}
      options={[{ value: "mosaic", label: "CONUS mosaic" }, { value: "nearest", label: "Nearest site" }, ...(sites.data?.sites ?? []).map((item) => ({ value: item.id, label: item.name === undefined ? item.id : `${item.id} · ${item.name}`, detail: `${item.country === "AU" ? "Australia · " : ""}${item.latitude.toFixed(2)}°, ${item.longitude.toFixed(2)}°` }))]}
      onChange={(value) => {
        const state = useMapStore.getState();
        const next = value === "mosaic" ? "mosaic" : "site";
        if (panelWeather(state).radarScope !== next) state.setRadarScope(next);
        state.setRadarSite(value === "nearest" || value === "mosaic" ? null : value);
        const selectedSite = sites.data?.sites.find((item) => item.id === value);
        if (selectedSite !== undefined) state.requestFocus(selectedSite.id, { latitude: selectedSite.latitude, longitude: selectedSite.longitude });
      }} />
    <SourceSelect label={`Site Product${panel > 0 ? ` · ${String(panel + 1)}` : ""}`} toolbarIcon={Radar} value={selected?.key ?? ""} disabled={products.length === 0}
      options={products.length === 0 ? [{ value: "", label: catalog.isError ? "Unavailable" : "Loading…" }] : products.map((item) => ({ value: item.key, label: item.label, detail: item.units }))}
      onChange={useMapStore.getState().setRadarProduct} />
    <div ref={scansRef} className={styles.scanSlot} />
    {tiltable && selected !== undefined ? <RadarTiltSelect label="Site Tilt" toolbarIcon={Layers} site={effectiveSite} product={selected.key} tilt={tilt} angle={scan?.elevationAngle} />
      : <button type="button" className={controls.trigger} aria-label="Site Tilt" aria-description={scope === "site" && tiltLabel === "Lowest available" ? "This radar provides its lowest available elevation." : "Choose Base Reflectivity, Velocity or Correlation Coefficient to select an elevation. Accumulations and Enhanced Echo Tops use the full volume."} disabled>
        <Layers className="icon-sm" aria-hidden="true" /><span className={controls.triggerCopy}><span className={controls.name}>Site Tilt</span><strong>{tiltLabel}</strong></span>
      </button>}
  </Surface>;
}

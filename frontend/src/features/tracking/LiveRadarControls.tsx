import { Antenna, Layers, Radar } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useEffectiveRadarSite, useMapCatalog, useRadarSiteFrame, useRadarSites } from "../../map/mapQueries";
import { panelWeather, useMapStore } from "../../stores/mapStore";
import { Surface } from "../../ui/Surface";
import { RadarTiltSelect } from "./RadarTiltSelect";
import { RADAR_TILTABLE_PRODUCTS } from "../../types/map";
import { useWeatherTime } from "./useWeatherTime";
import { SourceSelect } from "../sounding/SourceFields";
import controls from "../toolbar/ForecastControls.module.css";
import styles from "./LiveRadarControls.module.css";

export function LiveRadarControls({ className }: { className?: string | undefined }): ReactNode {
  const panel = useMapStore((state) => state.activePanel);
  const { radarScope: scope, radarTilt: tilt, radarSite: site, radarProduct: product } = useMapStore((state) => panelWeather(state));
  const catalog = useMapCatalog();
  const sites = useRadarSites(true);
  const products = (scope === "site" ? catalog.data?.siteProducts : catalog.data?.radarProducts) ?? [];
  const selected = products.find((item) => item.key === product) ?? products[0];
  const tiltable = selected !== undefined && scope === "site" && RADAR_TILTABLE_PRODUCTS.some((key) => key === selected.key);
  const effectiveSite = useEffectiveRadarSite();
  const scan = useRadarSiteFrame(product, effectiveSite, false, useWeatherTime(), tilt).data;
  const tiltLabel = scope === "mosaic" && selected?.key === "composite-reflectivity" ? "All tilts"
    : selected?.key === "echo-tops" ? "Full volume"
    : selected?.key === "reflectivity" || selected?.key === "base-reflectivity" || selected?.key === "velocity" || selected?.key === "hydrometeor" ? "Lowest available" : "Not applicable";
  return <Surface className={cx(controls.fields, styles.fields, className)} data-motion="fade" role="group" aria-label="Live radar" aria-description={`Controls for Panel ${String(panel + 1)}. Available elevations depend on the radar and its scan pattern.`} data-testid="live-radar-fields">
    <SourceSelect label="Site" toolbarIcon={Antenna} value={scope === "mosaic" ? "mosaic" : site ?? "nearest"}
      options={[{ value: "mosaic", label: "CONUS mosaic" }, { value: "nearest", label: "Nearest site" }, ...(sites.data?.sites ?? []).map((item) => ({ value: item.id, label: item.id, detail: `${item.latitude.toFixed(2)}°, ${item.longitude.toFixed(2)}°` }))]}
      onChange={(value) => {
        const state = useMapStore.getState();
        const next = value === "mosaic" ? "mosaic" : "site";
        if (panelWeather(state).radarScope !== next) state.setRadarScope(next);
        state.setRadarSite(value === "nearest" || value === "mosaic" ? null : value);
      }} />
    <SourceSelect label={`Site Product${panel > 0 ? ` · ${String(panel + 1)}` : ""}`} toolbarIcon={Radar} value={selected?.key ?? ""} disabled={products.length === 0}
      options={products.length === 0 ? [{ value: "", label: catalog.isError ? "Unavailable" : "Loading…" }] : products.map((item) => ({ value: item.key, label: item.label, detail: item.units }))}
      onChange={useMapStore.getState().setRadarProduct} />
    {tiltable ? <RadarTiltSelect label="Site Tilt" toolbarIcon={Layers} site={effectiveSite} product={selected.key} tilt={tilt} angle={scan?.elevationAngle} />
      : <button type="button" className={controls.trigger} aria-label="Site Tilt" aria-description="Choose Base Reflectivity, Velocity or Correlation Coefficient to select an elevation. Accumulations and Enhanced Echo Tops use the full volume." disabled>
        <Layers className="icon-sm" aria-hidden="true" /><span className={controls.triggerCopy}><span className={controls.name}>Site Tilt</span><strong>{tiltLabel}</strong></span>
      </button>}
  </Surface>;
}

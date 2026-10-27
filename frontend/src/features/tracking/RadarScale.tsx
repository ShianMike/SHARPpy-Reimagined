import type { ReactNode } from "react";
import { Eye, EyeOff, LoaderCircle, TriangleAlert } from "lucide-react";
import { cx } from "../../lib/cx";
import { displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { panelLayers, panelWeather, useMapStore } from "../../stores/mapStore";
import { Surface } from "../../ui/Surface";
import { IconAction } from "../../ui/Controls";
import { useEffectiveRadarSite, useMapCatalog, useRadarFrame, useRadarSiteFrame } from "../../map/mapQueries";
import { useWeatherTime } from "./useWeatherTime";
import { formatUtc } from "../sounding/soundingTime";
import { FieldLegend } from "../../map/controls/FieldLegend";
import { parsePalette } from "../../lib/contracts/map";
import reflectivityPalette from "@contracts/public/map/radar-reflectivity.palette.json";
import baseReflectivityPalette from "@contracts/public/map/radar-base-reflectivity.palette.json";
import correlationPalette from "@contracts/public/map/radar-correlation.palette.json";
import echoTopsPalette from "@contracts/public/map/radar-echo-tops.palette.json";
import scaleStyles from "../../map/controls/ParameterScale.module.css";
import hints from "../../ui/HoverHint.module.css";
import styles from "./RadarScale.module.css";

// https://opengeo.ncep.noaa.gov/geoserver/styles/reflectivity.png
// shortcut: NOAA's 500-pixel key places 0 dBZ at 137.5 and 10 dBZ 50 pixels later; re-sample if that key changes.
const REFLECTIVITY = parsePalette(reflectivityPalette, "radarReflectivity");
const BASE_REFLECTIVITY = parsePalette(baseReflectivityPalette, "baseReflectivity");
const CORRELATION = parsePalette(correlationPalette, "radarCorrelation");
const ECHO_TOPS = parsePalette(echoTopsPalette, "radarEchoTops");

export function RadarScale({ panel, horizontal = false }: { panel: number; horizontal?: boolean }): ReactNode {
  const preferences = useUiStore((state) => state.units);
  const visible = useMapStore((state) => panelLayers(state, panel).radar.visible);
  const { radarProduct: product, radarTilt: tilt, radarScope: scope } = useMapStore((state) => panelWeather(state, panel));
  const time = useWeatherTime();
  const site = useEffectiveRadarSite(panel);
  const mosaic = useRadarFrame(product, visible && scope === "mosaic", time);
  const antenna = useRadarSiteFrame(product, site, visible && scope === "site", time, tilt);
  const query = scope === "site" ? antenna : mosaic;
  const frame = query.data;
  const catalog = useMapCatalog().data;
  const products = scope === "site" ? catalog?.siteProducts : catalog?.radarProducts;
  const selected = products?.find((item) => item.key === product) ?? products?.[0];
  const label = frame?.label ?? selected?.label ?? "Radar";
  const units = frame?.units ?? selected?.units ?? "";
  const palette = frame?.palette ?? (scope === "site" && selected?.key === "correlation-coefficient" ? CORRELATION
    : scope === "site" && selected?.key === "echo-tops" ? ECHO_TOPS : units === "dBZ" ? scope === "site" ? BASE_REFLECTIVITY : REFLECTIVITY : null);
  const shownUnits = palette === null ? units : displayUnit(palette.units, preferences, palette.display);
  const description = selected?.key === "correlation-coefficient"
    ? "Correlation Coefficient (ρHV), a unitless ratio. Lower values indicate less similar targets. Values above 1 can occur from measurement noise; values outside the scale use its end colors."
    : selected?.key === "echo-tops" ? "Enhanced Echo Tops: height of the 18 dBZ echo in thousands of feet above sea level. Some heights are lower bounds where echoes extend above the highest scan. Values outside the scale use its end colors."
    : units === "dBZ" ? scope === "site" ? "Base Reflectivity in dBZ · BR HiRes color table. Values outside the scale use the end colors." : "Radar reflectivity in dBZ, following NOAA's labelled color key."
    : selected?.key === "velocity" ? "Negative values move toward the radar; positive values move away. Values outside the scale use the end colors. Purple means range folded. This color table does not undo velocity aliasing."
    : selected?.key === "hydrometeor" ? "Hydrometeor categories use the provider's labelled color key."
    : "Precipitation accumulations use the provider's labelled color key and units.";
  const scan = frame?.validTime == null ? "" : `Scan ${formatUtc(frame.validTime)} UTC`;
  const age = Date.parse(time ?? "") - Date.parse(frame?.validTime ?? "");
  const delay = age > 10 * 60_000 ? `Radar scan is ${String(Math.floor(age / 60_000))} minutes behind the selected time` : null;
  const status = !visible ? null : query.isError ? "Unavailable" : query.isFetching ? "Loading…" : delay;
  const StatusIcon = query.isError || !query.isFetching ? TriangleAlert : LoaderCircle;
  if (horizontal) return <div className={styles.inline} aria-label="Radar color key">
    <strong>{label}{shownUnits ? ` · ${shownUnits}` : ""}</strong>
    {palette !== null ? <FieldLegend palette={palette} description={description} /> : frame?.legendPng
      ? <img src={frame.legendPng} alt={`${label} · NOAA color scale`} /> : <p>Provider color key unavailable.</p>}
    {palette === null ? <p>{description}</p> : null}{scan === "" ? null : <p>{scan}</p>}{status === null ? null : <p>{status}</p>}
  </div>;
  return <Surface as="aside" data-motion="fade" className={scaleStyles.panel} aria-label="Radar scale" aria-description={`${label} · ${shownUnits || (palette === null ? "Categories" : "Unitless")} · Panel ${String(panel + 1)}${scan === "" ? "" : ` · ${scan}`}`} data-testid="radar-scale">
    {palette === null ? <div className={cx(styles.provider, hints.target)} tabIndex={0} role="img" aria-label={`${label} color scale, ${units || "categories"}`} aria-description="Focus or hover to see the provider's labelled color key.">
      <div className={styles.ramp} aria-hidden="true">{frame?.legendPng === undefined || frame.legendPng === null ? null : <img src={frame.legendPng} alt="" />}</div>
      <span className={cx(hints.hint, styles.key)} data-side="left" role="tooltip"><strong>{label}{units ? ` · ${units}` : ""}</strong>{frame?.legendPng ? <img src={frame.legendPng} alt={`${label} · NOAA color scale`} /> : <span>{status ?? (visible ? "Provider color key unavailable." : "Enable the layer to load its color key.")}</span>}</span>
    </div> : <FieldLegend palette={palette} vertical description={`${description}${scan === "" ? "" : ` ${scan}.`}`} />}
    {status === null ? null : <span className={cx(scaleStyles.status, hints.target)} tabIndex={0} role="img" aria-label={status}><StatusIcon className="icon-sm" data-loading={query.isFetching && !query.isError} aria-hidden="true" /><span className={hints.hint} data-side="left" aria-hidden="true">{label} · {status}{scan === "" ? "" : ` · ${scan}`}</span></span>}
    <IconAction className={scaleStyles.visibility} size="sm" icon={visible ? Eye : EyeOff} label="Radar layer" aria-pressed={visible} aria-description={visible ? "Layer on. Activate to hide." : "Layer off. Activate to show."} hintSide="left" onClick={() => {
      const state = useMapStore.getState();
      state.setActivePanel(panel);
      state.setLayerVisible("radar", !panelLayers(state, panel).radar.visible);
    }} />
  </Surface>;
}

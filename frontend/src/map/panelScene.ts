// What one map panel draws, from the queries to its runtime (REQUEST §7 to
// §11): the stations, the coloured field, the SPC outlooks of the forecast
// valid time, the radar mosaic, the model domain, and the place names. Shared
// layers come from shared queries, so four panels fetch each thing once.

import { useEffect, useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { useForecastContext } from "../features/forecast/useForecast";
import { useWeatherTime } from "../features/tracking/useWeatherTime";
import { useStationCatalog } from "../stations/stationQueries";
import { panelLayers, panelWeather, useMapStore } from "../stores/mapStore";
import type { Station } from "../types/contracts";
import { OUTLOOK_KEYS, type FieldFrame, type Outlook, type OutlookKey, type Palette, type Place, type RadarFrame, type RadarSite } from "../types/map";
import type { ForecastModel } from "../types/soundings";
import { fieldImage } from "./fieldImages";
import type { FieldImage, RasterImage } from "./layers/registry";
import { useFieldContours } from "./contourQueries";
import { useEffectiveRadarSite, usePlaceLabels, useRadarFrame, useRadarSiteFrame, useRadarSites, useSatelliteFrame, useStormReports, useSpcOutlooks } from "./mapQueries";
import { useSelectionAnchor, useSurfacePlots } from "./surfaceQueries";
import { usePanelField, type PanelField } from "./panelField";
import type { MapRuntime } from "./runtime/MapRuntime";
import { useFieldTransition } from "./useFieldTransition";
import { useWeatherAlerts } from "./weatherAlertsQuery";
import { cachedPng, decodePng } from "./pngImages";

const NO_STATIONS: readonly Station[] = [];
const NO_PLACES: readonly Place[] = [];
const NO_RADAR_SITES: readonly RadarSite[] = [];
type Path = readonly (readonly [number, number])[];

const domains = new WeakMap<ForecastModel, Path | null>();

/** The model's grid perimeter, closed; its bounds when only those are known; null for a global model. */
export function domainPath(model: ForecastModel): Path | null {
  if (domains.has(model)) {
    return domains.get(model) ?? null;
  }
  const { outline, bounds } = model;
  const first = outline[0];
  let path: Path | null = null;
  if (outline.length >= 3 && first !== undefined) {
    const last = outline.at(-1);
    path = last?.[0] === first[0] && last[1] === first[1] ? outline : [...outline, first];
  } else if (bounds.east - bounds.west < 359) {
    const { west, east, south, north } = bounds;
    path = [[west, south], [east, south], [east, north], [west, north], [west, south]];
  }
  domains.set(model, path);
  return path;
}

/** Share a frame; colour it only if Sharp uses it, then release it with the query's frame. */
export function useFieldImage(frame: FieldFrame | undefined, palette: Palette | undefined): FieldImage | null {
  return useMemo(() => frame === undefined || palette === undefined ? null : fieldImage(frame, palette), [frame, palette]);
}

/** Any backend frame that arrives as one PNG over lon/lat bounds. */
type PngFrame = Pick<RadarFrame, "png" | "bounds" | "validTime">;

/** Decode once across panels; keep this query's last good image during refresh errors. */
function usePngImage(query: Pick<UseQueryResult<PngFrame>, "data">, enabled: boolean, source: string): RasterImage | null {
  const frame = enabled ? query.data : undefined;
  const [decoded, setDecoded] = useState<{ frame: PngFrame; source: string; image: RasterImage } | null>(null);
  useEffect(() => {
    if (frame === undefined) {
      return;
    }
    let live = true;
    decodePng(frame.png).then(
      (image) => {
        if (live) {
          setDecoded({ frame, source, image: { image, bounds: frame.bounds } });
        }
      },
      (error: unknown) => {
        console.warn("[map] the frame could not be decoded", error);
      },
    );
    return () => {
      live = false;
    };
  }, [frame, source]);
  const ready = frame === undefined ? undefined : cachedPng(frame.png);
  return useMemo(() => {
    if (frame === undefined) return null;
    if (ready !== undefined) return { image: ready, bounds: frame.bounds };
    const age = Date.parse(frame.validTime ?? "") - Date.parse(decoded?.frame.validTime ?? "");
    return decoded?.source === source && (decoded.frame === frame || age >= 0 && age <= 10 * 60_000) ? decoded.image : null;
  }, [frame, ready, decoded, source]);
}

/** The outlooks this panel has loaded, keyed by product; a stable object while they do not change. */
function useOutlooks(panel: number, validTime: string | null): Readonly<Partial<Record<OutlookKey, Outlook>>> {
  const visible = useMapStore((state) => {
    const layers = panelLayers(state, panel);
    return OUTLOOK_KEYS.map((key) => layers[`spc-${key}` as const].visible).join();
  });
  const queries = useSpcOutlooks(OUTLOOK_KEYS.map((key, i) => visible.split(",")[i] === "true" && validTime !== null), validTime);
  const [cat, torn, wind, hail] = queries.map((query) =>
    validTime !== null && !query.isError && query.data?.outlook?.covers === true ? query.data.outlook : undefined,
  );
  return useMemo(() => {
    const out: Partial<Record<OutlookKey, Outlook>> = {};
    for (const [key, outlook] of [["cat", cat], ["torn", torn], ["wind", wind], ["hail", hail]] as const) {
      if (outlook !== undefined) {
        out[key] = outlook;
      }
    }
    return out;
  }, [cat, torn, wind, hail]);
}

/** Feeds a panel's runtime; returns the panel's field for its chip. */
export function usePanelScene(runtime: MapRuntime | null, panel: number): PanelField {
  const field = usePanelField(panel);
  const image = useFieldImage(field.request === null || field.frame.isError ? undefined : field.frame.data, field.product?.palette);
  const stations = useStationCatalog().data?.stations ?? NO_STATIONS;
  const context = useForecastContext();
  const validTime = useWeatherTime();
  const live = useMapStore((state) => state.view === "live");
  const layers = useMapStore((state) => panelLayers(state, panel));
  const radarSites = useRadarSites(live && layers["radar-site-labels"].visible).data?.sites ?? NO_RADAR_SITES;
  const radarVisible = layers.radar.visible;
  const reportsVisible = layers["storm-reports"].visible;
  const surfaceVisible = layers["surface-plots"].visible;
  const placesVisible = layers["place-labels"].visible;
  const { radarProduct, radarTilt: tilt, radarScope: scope, satelliteChannel: channel } = useMapStore((state) => panelWeather(state, panel));
  const reportWindowS = useMapStore((state) => state.reportWindowS);
  const reportFilter = useMapStore((state) => state.reportFilter);
  const site = useEffectiveRadarSite(panel);
  const mosaic = usePngImage(useRadarFrame(radarProduct, live && radarVisible && scope === "mosaic", live ? validTime : null), live && radarVisible && scope === "mosaic", radarProduct ?? "default");
  const siteFrame = usePngImage(useRadarSiteFrame(radarProduct, site, live && radarVisible && scope === "site", live ? validTime : null, tilt), live && radarVisible && scope === "site", `${site ?? ""}/${radarProduct ?? "reflectivity"}/${tilt}`);
  const radar = scope === "site" ? siteFrame : mosaic;
  const satellite = usePngImage(useSatelliteFrame(channel, validTime, live && layers.satellite.visible), live && layers.satellite.visible, channel);
  const alertQuery = useWeatherAlerts(validTime, live && (layers.warnings.visible || layers.watches.visible));
  const alerts = live && !alertQuery.isError && (layers.warnings.visible || layers.watches.visible) ? alertQuery.data?.alerts ?? null : null;
  const reportQuery = useStormReports(validTime, reportWindowS, reportFilter, !live && reportsVisible);
  const reports = !live && reportsVisible && !reportQuery.isError ? reportQuery.data?.reports ?? null : null;
  const surfaceAnchor = useSelectionAnchor();
  const surfaceQuery = useSurfacePlots(surfaceAnchor, validTime, live && surfaceVisible);
  const surfacePlots = !live || !surfaceVisible || surfaceQuery.isError ? null : surfaceQuery.data?.plots ?? null;
  const contourSpec = live ? null : field.product?.contour ?? null;
  const contourQuery = useFieldContours(
    field.request?.product ?? null,
    field.request?.runTime ?? null,
    field.request?.forecastHour ?? null,
    (contourSpec !== null || field.product?.wind === true) && field.request !== null,
    field.request?.model,
  );
  const contourLines = contourQuery.isError ? null : contourQuery.data ?? null;
  const outlooks = useOutlooks(panel, live ? null : validTime);
  const places = usePlaceLabels(import.meta.env.MODE === "fixture" && placesVisible).data?.places ?? NO_PLACES;
  const model = live ? undefined : context?.model;
  const domain = useMemo(() => (model === undefined ? null : domainPath(model)), [model]);

  useEffect(() => {
    runtime?.setStations(stations);
  }, [runtime, stations]);
  useFieldTransition(runtime, image, model?.key);
  useEffect(() => {
    runtime?.setScene({ radar, satellite, reports, alerts, surfacePlots, contourSpec, contours: contourLines, outlooks, domain, radarSites });
  }, [runtime, radar, satellite, reports, alerts, surfacePlots, contourSpec, contourLines, outlooks, domain, radarSites]);
  useEffect(() => {
    runtime?.setScene({ places });
  }, [runtime, places]);
  return field;
}

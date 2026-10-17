// What one map panel draws, from the queries to its runtime (REQUEST §7 to
// §11): the stations, the coloured field, the SPC outlooks of the forecast
// valid time, the radar mosaic, the model domain, and the place names. Shared
// layers come from shared queries, so four panels fetch each thing once.

import { useEffect, useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { useForecastContext } from "../features/forecast/useForecast";
import { useStationCatalog } from "../stations/stationQueries";
import { panelLayers, useMapStore } from "../stores/mapStore";
import type { Station } from "../types/contracts";
import { OUTLOOK_KEYS, type FieldFrame, type Outlook, type OutlookKey, type Palette, type Place, type RadarFrame } from "../types/map";
import type { ForecastModel } from "../types/soundings";
import { colourize } from "./field/colourize";
import type { FieldImage, RasterImage } from "./layers/registry";
import { useFieldContours } from "./contourQueries";
import { useEffectiveRadarSite, usePlaceLabels, useRadarFrame, useRadarSiteFrame, useSatelliteFrame, useStormReports, useSpcOutlooks } from "./mapQueries";
import { useSelectionAnchor, useSurfacePlots } from "./surfaceQueries";
import { usePanelField, type PanelField } from "./panelField";
import type { MapRuntime } from "./runtime/MapRuntime";
import { useFieldTransition } from "./useFieldTransition";

const NO_STATIONS: readonly Station[] = [];
const NO_PLACES: readonly Place[] = [];
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

const fieldImages = new WeakMap<FieldFrame, { palette: Palette; image: FieldImage }>();

/** Share a frame; colour it only if Sharp uses it, then release it with the query's frame. */
export function useFieldImage(frame: FieldFrame | undefined, palette: Palette | undefined): FieldImage | null {
  return useMemo(() => {
    if (frame === undefined || palette === undefined) return null;
    const cached = fieldImages.get(frame);
    if (cached?.palette === palette) return cached.image;
    let coloured: ImageData | undefined;
    const image: FieldImage = {
      get image() { return coloured ??= new ImageData(colourize(frame, palette), frame.width, frame.height); },
      bounds: frame.bounds, frame, palette,
    };
    fieldImages.set(frame, { palette, image });
    return image;
  }, [frame, palette]);
}

/** Any backend frame that arrives as one PNG over lon/lat bounds. */
type PngFrame = Pick<RadarFrame, "png" | "bounds">;

const bitmaps = new WeakMap<PngFrame, Promise<ImageBitmap>>();

/** Decode once across panels; keep this query's last good image during refresh errors. */
function usePngImage(query: Pick<UseQueryResult<PngFrame>, "data">): RasterImage | null {
  const frame = query.data;
  const [decoded, setDecoded] = useState<{ frame: PngFrame; image: RasterImage } | null>(null);
  useEffect(() => {
    if (frame === undefined) {
      return;
    }
    let live = true;
    let bitmap = bitmaps.get(frame);
    if (bitmap === undefined) {
      bitmap = createImageBitmap(frame.png);
      bitmaps.set(frame, bitmap);
    }
    bitmap.then(
      (image) => {
        if (live) {
          setDecoded({ frame, image: { image, bounds: frame.bounds } });
        }
      },
      (error: unknown) => {
        console.warn("[map] the frame could not be decoded", error);
      },
    );
    return () => {
      live = false;
    };
  }, [frame]);
  return decoded !== null && decoded.frame === frame ? decoded.image : null;
}

/** The outlooks this panel has loaded, keyed by product; a stable object while they do not change. */
function useOutlooks(panel: number, validTime: string | null): Readonly<Partial<Record<OutlookKey, Outlook>>> {
  const visible = useMapStore((state) => {
    const layers = panelLayers(state, panel);
    return OUTLOOK_KEYS.map((key) => layers[`spc-${key}` as const].visible).join();
  });
  const queries = useSpcOutlooks(OUTLOOK_KEYS.map((key, i) => visible.split(",")[i] === "true" && validTime !== null), validTime);
  const [cat, torn, wind, hail] = queries.map((query) =>
    !query.isError && query.data?.outlook?.covers === true ? query.data.outlook : undefined,
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
  const image = useFieldImage(field.frame.isError ? undefined : field.frame.data, field.product?.palette);
  const stations = useStationCatalog().data?.stations ?? NO_STATIONS;
  const context = useForecastContext();
  const layers = useMapStore((state) => panelLayers(state, panel));
  const radarVisible = layers.radar.visible;
  const reportsVisible = layers["storm-reports"].visible;
  const surfaceVisible = layers["surface-plots"].visible;
  const placesVisible = layers["place-labels"].visible;
  const radarProduct = useMapStore((state) => state.radarProduct);
  const scope = useMapStore((state) => state.radarScope);
  const channel = useMapStore((state) => state.satelliteChannel);
  const reportWindowS = useMapStore((state) => state.reportWindowS);
  const reportFilter = useMapStore((state) => state.reportFilter);
  const site = useEffectiveRadarSite();
  const mosaic = usePngImage(useRadarFrame(radarProduct, radarVisible && scope === "mosaic"));
  const siteFrame = usePngImage(useRadarSiteFrame(radarProduct, site, radarVisible && scope === "site"));
  const radar = scope === "site" ? siteFrame : mosaic;
  const satellite = usePngImage(useSatelliteFrame(channel, context?.validTime ?? null, layers.satellite.visible));
  const reportQuery = useStormReports(context?.validTime ?? null, reportWindowS, reportFilter, reportsVisible);
  const reports = reportQuery.isError ? null : reportQuery.data?.reports ?? null;
  const surfaceAnchor = useSelectionAnchor();
  const surfaceQuery = useSurfacePlots(surfaceAnchor, context?.validTime ?? null, surfaceVisible);
  const surfacePlots = surfaceQuery.isError ? null : surfaceQuery.data?.plots ?? null;
  const contourSpec = field.product?.contour ?? null;
  const contourQuery = useFieldContours(
    field.request?.product ?? null,
    field.request?.runTime ?? null,
    field.request?.forecastHour ?? null,
    (contourSpec !== null || field.product?.wind === true) && field.request !== null,
    field.request?.model,
  );
  const contourLines = contourQuery.isError ? null : contourQuery.data ?? null;
  const outlooks = useOutlooks(panel, context?.validTime ?? null);
  const places = usePlaceLabels(import.meta.env.MODE === "fixture" && placesVisible).data?.places ?? NO_PLACES;
  const model = context?.model;
  const domain = useMemo(() => (model === undefined ? null : domainPath(model)), [model]);

  useEffect(() => {
    runtime?.setStations(stations);
  }, [runtime, stations]);
  useFieldTransition(runtime, image, model?.key);
  useEffect(() => {
    runtime?.setScene({ radar, satellite, reports, surfacePlots, contourSpec, contours: contourLines, outlooks, domain });
  }, [runtime, radar, satellite, reports, surfacePlots, contourSpec, contourLines, outlooks, domain]);
  useEffect(() => {
    runtime?.setScene({ places });
  }, [runtime, places]);
  return field;
}

// Geography drawn by deck.gl (REQUEST §10, Phase 2): the selected model's
// domain, place names, and the fixture basemap boundaries over the weather
// layers. The overlay draws in its own canvas above MapLibre's, so without
// that second pass a field at full opacity would hide every border.

import { GeoJsonLayer, PathLayer, TextLayer } from "@deck.gl/layers";
import type { Place } from "../../types/map";
import { BASEMAP_FAMILIES, WIDTH } from "../basemap/basemapLayers";
import { basemapSourceUrl } from "../basemap/basemapStyle";
import type { LayerBuildContext, LayerState } from "./registry";
import { deckColour } from "./StationLayer";

export const DOMAIN_LAYER_ID = "model-domain";
export const PLACE_LABELS_LAYER_ID = "place-labels";

interface DomainPath {
  path: readonly (readonly [number, number])[];
}

const domainData = new WeakMap<object, DomainPath[]>();

function domainPaths(domain: readonly (readonly [number, number])[]): DomainPath[] {
  let data = domainData.get(domain);
  if (data === undefined) {
    data = [{ path: domain }];
    domainData.set(domain, data);
  }
  return data;
}

export function buildDomainLayers(ctx: LayerBuildContext, state: LayerState): PathLayer<DomainPath>[] {
  if (ctx.domain === null) {
    return [];
  }
  return [
    new PathLayer<DomainPath>({
      id: DOMAIN_LAYER_ID,
      data: domainPaths(ctx.domain),
      getPath: (item) => item.path.map((point): [number, number] => [point[0], point[1]]),
      getColor: deckColour(ctx.tokens.colours.domain),
      widthUnits: "pixels",
      getWidth: ctx.tokens.lengths.domainWidth,
      wrapLongitude: true,
      pickable: false,
      visible: state.visible,
      opacity: state.opacity,
    }),
  ];
}

/** "New York, New York" is drawn as "New York". */
export function placeText(place: Pick<Place, "name">): string {
  return place.name.split(",")[0]?.trim() ?? place.name;
}

export function buildPlaceLabels(ctx: LayerBuildContext, state: LayerState): TextLayer<Place>[] {
  // Live maps use OpenFreeMap's labels; offline fixtures keep their local place catalogue.
  if (import.meta.env.MODE !== "fixture") return [];
  const { colours, lengths } = ctx.tokens;
  return [
    new TextLayer<Place>({
      id: PLACE_LABELS_LAYER_ID,
      data: ctx.placeLabels,
      getPosition: (place) => [place.longitude, place.latitude],
      getText: placeText,
      getColor: deckColour(colours.placeText),
      getSize: lengths.placeLabelSize,
      sizeUnits: "pixels",
      getTextAnchor: "start",
      getAlignmentBaseline: "center",
      getPixelOffset: [lengths.stationRadius + lengths.stationEdge, 0],
      fontFamily: ctx.labelFont,
      fontSettings: { sdf: true },
      outlineWidth: lengths.labelHaloWidth,
      outlineColor: deckColour(colours.labelHalo),
      characterSet: "auto",
      pickable: false,
      visible: state.visible && ctx.placeLabels.length > 0,
      opacity: state.opacity,
    }),
  ];
}

/** Keep ocean coastlines above weather; live political borders are drawn by MapLibre. */
export function buildBoundaryLayers(ctx: LayerBuildContext, state: LayerState): GeoJsonLayer[] {
  if (!ctx.weatherShown || !ctx.basemap.visible || !state.visible) {
    return [];
  }
  return BASEMAP_FAMILIES.filter((family) => family !== "lakes" && (import.meta.env.MODE === "fixture" || family === "coastline")).map(
    (family) =>
      new GeoJsonLayer({
        id: `boundaries-${family}`,
        data: basemapSourceUrl(family),
        stroked: true,
        filled: false,
        lineWidthUnits: "pixels",
        getLineWidth: ctx.tokens.lengths[WIDTH[family]],
        getLineColor: deckColour(ctx.tokens.colours[family]),
        pickable: false,
        opacity: ctx.basemap.opacity * state.opacity,
      }),
  );
}

// Basemap style layers (DESIGN-frontend F4): the background, then states,
// countries, lakes, and coastline lines, coloured and sized from the --map-*
// tokens. MapLibre types only; nothing here loads maplibre-gl.

import type { LayerSpecification, LineLayerSpecification, Map as MapLibreMap } from "maplibre-gl";
import { documentTokenReader, parseLength, type CanvasTokens, type Rgba } from "../canvasTokens";
import type { LayerState } from "../layers/registry";

/** Drawn bottom to top. */
export const BASEMAP_FAMILIES = ["states", "countries", "lakes", "coastline"] as const;
export type BasemapFamily = (typeof BASEMAP_FAMILIES)[number];

export const BACKGROUND_LAYER_ID = "background";

/** The style layer of a family; the e2e hooks count rendered features per id. */
export function basemapLayerId(family: BasemapFamily): string {
  return `basemap-${family}`;
}

export const BASEMAP_LINE_LAYER_IDS: readonly string[] = BASEMAP_FAMILIES.map(basemapLayerId);

const nativeStyles = new WeakMap<MapLibreMap, { layers: LayerSpecification[]; opacity: number | undefined }>();

/** Keep native roads; land edges and political borders stay above weather. */
export function prepareBasemap(map: MapLibreMap): string | undefined {
  const dark = document.documentElement.dataset.theme === "dark";
  const read = documentTokenReader();
  const layers = map.getStyle().layers.filter((layer) => !layer.id.startsWith("geography-"));
  for (const layer of layers) {
    if (dark && layer.type === "background") map.setPaintProperty(layer.id, "background-color", read("--basemap-background").trim());
    if (dark && layer.type === "fill") {
      if (layer["source-layer"] === "water") map.setPaintProperty(layer.id, "fill-color", read("--basemap-water").trim());
      if (layer.id === "landuse_residential") map.setPaintProperty(layer.id, "fill-color", read("--basemap-land").trim());
      if (layer["source-layer"] === "building") {
        map.setPaintProperty(layer.id, "fill-color", read("--basemap-land").trim());
        map.setPaintProperty(layer.id, "fill-outline-color", read("--basemap-building-edge").trim());
      }
    }
    if (dark && layer.type === "line" && layer["source-layer"] === "transportation") {
      map.setPaintProperty(layer.id, "line-color", read(layer.id.includes("casing") ? "--basemap-land" : "--basemap-road").trim());
    }
    if (layer.type === "line" && layer["source-layer"] === "boundary") {
      const state = layer.id === "boundary_3" || layer.id.includes("state");
      map.setPaintProperty(layer.id, "line-width", ["interpolate", ["linear"], ["zoom"], 3, state ? 1.25 : 1.55, 8, state ? 1.6 : 2, 14, state ? 2 : 2.5]);
      map.setPaintProperty(layer.id, "line-blur", 0);
      if (dark) map.setPaintProperty(layer.id, "line-color", read(state ? "--basemap-state" : "--basemap-country").trim());
    }
    if (dark && layer.type === "line" && layer["source-layer"] === "waterway") map.setPaintProperty(layer.id, "line-color", read("--basemap-waterway").trim());
    if (dark && layer.type === "symbol" && ["place", "water_name", "transportation_name"].includes(layer["source-layer"] ?? "")) {
      map.setPaintProperty(layer.id, "text-color", read("--basemap-label").trim());
      map.setPaintProperty(layer.id, "text-halo-color", read("--basemap-label-halo").trim());
      map.setPaintProperty(layer.id, "text-halo-blur", 0);
      map.setPaintProperty(layer.id, "text-halo-width", 1.25);
    }
  }
  const water = layers.find((layer) => layer.type === "fill" && layer["source-layer"] === "water");
  if (dark && map.getSource("ne2_shaded") !== undefined && map.getLayer("geography-relief") === undefined) {
    map.addLayer({ id: "geography-relief", type: "raster", source: "ne2_shaded", maxzoom: 10,
      paint: { "raster-saturation": -1, "raster-brightness-max": 0.3, "raster-contrast": 0.2,
        "raster-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.5, 6, 0.35, 10, 0] },
    }, water?.id);
  }
  const outlines = map.getStyle().layers.filter((layer): layer is LineLayerSpecification =>
    layer.type === "line" && !layer.id.endsWith("-halo") && layer["source-layer"] === "boundary");
  let beforeId: string | undefined;
  if (water?.type === "fill") {
    // shortcut: provider polygons can expose split edges; use a dedicated coastline source if seams appear.
    const shoreline: LineLayerSpecification = { ...water, id: "geography-shoreline", type: "line",
      filter: ["all", water.filter ?? true, ["==", ["get", "class"], "ocean"]] as NonNullable<LineLayerSpecification["filter"]>,
      layout: { ...water.layout, "line-cap": "round", "line-join": "round" },
      paint: { "line-color": read("--map-coastline").trim(), "line-width": parseLength(read("--map-coastline-width"), "--map-coastline-width") },
    };
    if (map.getLayer(shoreline.id) === undefined) map.addLayer(shoreline);
    map.moveLayer(shoreline.id);
    beforeId = shoreline.id;
  }
  for (const layer of outlines) {
    const halo: LineLayerSpecification = { ...layer, id: `geography-${layer.id}-halo`,
      filter: ["all", layer.filter ?? true, ["!=", ["get", "maritime"], 1]] as NonNullable<LineLayerSpecification["filter"]>,
      paint: { ...layer.paint,
      "line-color": read("--basemap-border-halo").trim(),
      "line-width": scaledPaint(layer.paint?.["line-width"] ?? 1, 1.7) as NonNullable<NonNullable<LineLayerSpecification["paint"]>["line-width"]>,
    } };
    if (map.getLayer(halo.id) === undefined) {
      map.setFilter(layer.id, halo.filter);
      map.addLayer(halo);
    }
    map.moveLayer(halo.id);
    beforeId ??= halo.id;
    map.moveLayer(layer.id);
    beforeId ??= layer.id;
  }
  for (const layer of layers) if (layer.type === "symbol") map.moveLayer(layer.id);
  nativeStyles.set(map, { layers: map.getStyle().layers, opacity: undefined });
  return beforeId;
}

function scaledPaint(value: unknown, factor: number): unknown {
  if (factor === 1) return value;
  if (typeof value === "number") return value * factor;
  // Zoom must remain the input of a top-level curve; scale its outputs instead.
  if (Array.isArray(value) && (value[0] === "interpolate" || value[0] === "step")) {
    const first = value[0] === "step" ? 2 : 4;
    return (value as unknown[]).map((part, i) => i >= first && (i - first) % 2 === 0 ? ["*", part, factor] : part);
  }
  return ["*", value, factor];
}

function hexByte(value: number): string {
  return Math.round(value).toString(16).padStart(2, "0");
}

/** A CSS colour string for MapLibre paint properties, from a token colour. */
export function cssColour(colour: Rgba): string {
  const [r, g, b, a] = colour;
  const alpha = a === 255 ? "" : hexByte(a);
  return "#" + hexByte(r) + hexByte(g) + hexByte(b) + alpha;
}

export const WIDTH: Readonly<Record<BasemapFamily, keyof CanvasTokens["lengths"]>> = {
  states: "statesWidth",
  countries: "countriesWidth",
  lakes: "lakesWidth",
  coastline: "coastlineWidth",
};

export function basemapLayers(tokens: CanvasTokens): LayerSpecification[] {
  const lines: LayerSpecification[] = BASEMAP_FAMILIES.map((family) => ({
    id: basemapLayerId(family),
    type: "line",
    source: family,
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": cssColour(tokens.colours[family]),
      "line-width": tokens.lengths[WIDTH[family]],
    },
  }));
  return [
    { id: BACKGROUND_LAYER_ID, type: "background", paint: { "background-color": cssColour(tokens.colours.background) } },
    ...lines,
  ];
}

/** Visibility and opacity of the original basemap (or the offline fixture lines). */
export function applyBasemapState(map: MapLibreMap, state: LayerState): void {
  if (map.getSource("openmaptiles") !== undefined) {
    const original = nativeStyles.get(map) ?? { layers: map.getStyle().layers, opacity: undefined };
    nativeStyles.set(map, original);
    const opacity = state.visible ? state.opacity : 0;
    // The canvas now includes weather: dim only the native style, without compounding its paints.
    if (original.opacity !== opacity) {
      for (const layer of original.layers) {
        const properties = layer.type === "symbol" ? ["text-opacity", "icon-opacity"] : [`${layer.type}-opacity`];
        for (const name of properties) {
          const property = name as Parameters<MapLibreMap["getPaintProperty"]>[1];
          const paint = layer.paint as Record<string, unknown> | undefined;
          const value = scaledPaint(paint?.[property] ?? 1, opacity);
          if (JSON.stringify(map.getPaintProperty(layer.id, property) ?? 1) !== JSON.stringify(value)) {
            map.setPaintProperty(layer.id, property, value as Parameters<MapLibreMap["setPaintProperty"]>[2]);
          }
        }
      }
      original.opacity = opacity;
    }
    // Match SoundScopepy: decorative park fills can look like weather returns.
    for (const id of ["park", "park_outline", "landcover_wood", "landcover_grass", "landuse_park"]) {
      if (map.getLayer(id) !== undefined && map.getLayoutProperty(id, "visibility") !== "none") {
        map.setLayoutProperty(id, "visibility", "none");
      }
    }
    return;
  }
  for (const id of BASEMAP_LINE_LAYER_IDS) {
    if (map.getLayer(id) === undefined) {
      continue;
    }
    const visibility = state.visible ? "visible" : "none";
    if ((map.getLayoutProperty(id, "visibility") ?? "visible") !== visibility) map.setLayoutProperty(id, "visibility", visibility);
    if ((map.getPaintProperty(id, "line-opacity") ?? 1) !== state.opacity) map.setPaintProperty(id, "line-opacity", state.opacity);
  }
}

/** Remaining native labels keep the original style's typography and zoom rules. */
export function applyBasemapLabels(map: MapLibreMap, state: LayerState): void {
  for (const layer of map.getStyle().layers) {
    if (layer.type !== "symbol" || layer.source !== "openmaptiles") continue;
    const visibility = state.visible ? "visible" : "none";
    if ((map.getLayoutProperty(layer.id, "visibility") ?? "visible") !== visibility) map.setLayoutProperty(layer.id, "visibility", visibility);
  }
}

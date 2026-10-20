// @vitest-environment jsdom
import type { LayerSpecification, LineLayerSpecification, Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import { readCanvasTokens } from "../canvasTokens";
import { applyBasemapLabels, applyBasemapState, prepareBasemap } from "./basemapLayers";
import { basemapSourceUrl, basemapStyle, MAP_STYLE_URLS } from "./basemapStyle";

const tokens = readCanvasTokens(cssTokenReader(tokensCss));
const style = document.createElement("style");
style.textContent = tokensCss;
beforeEach(() => { document.head.append(style); });
afterEach(() => {
  style.remove();
  vi.unstubAllEnvs();
  delete document.documentElement.dataset.theme;
  window.history.replaceState(null, "", "/");
});

it("uses SoundScopepy's original style for the resolved light and dark themes", () => {
  vi.stubEnv("MODE", "live");
  document.documentElement.dataset.theme = "light";
  expect(basemapStyle(tokens)).toBe(MAP_STYLE_URLS.light);
  document.documentElement.dataset.theme = "dark";
  expect(basemapStyle(tokens)).toBe(MAP_STYLE_URLS.dark);
});

it("keeps offline and missing-source geometry confined to fixtures", () => {
  vi.stubEnv("MODE", "fixture");
  window.history.replaceState(null, "", "/?scenario=basemap-missing");
  expect((basemapStyle(tokens) as StyleSpecification).sources.states).toMatchObject({
    type: "geojson", data: "/geo/missing-states.geojson",
  });
  vi.stubEnv("MODE", "live");
  expect(basemapSourceUrl("states")).toBe("/geo/states.geojson");
  expect(typeof basemapStyle(tokens)).toBe("string");
});

it.each(["light", "dark"])("keeps political borders over weather and inland water underneath in %s, respecting basemap opacity", (theme) => {
  document.documentElement.dataset.theme = theme;
  const canvas = document.createElement("canvas");
  const setLayoutProperty = vi.fn();
  const setPaintProperty = vi.fn();
  const layout = new Map<string, string>();
  const borderOpacity: NonNullable<LineLayerSpecification["paint"]>["line-opacity"] = ["interpolate", ["linear"], ["zoom"], 0, 0.4, 4, 1];
  const layers: LayerSpecification[] = [
    { id: "background", type: "background", paint: { "background-color": "#f8f4f0" } },
    { id: "water", type: "fill", source: "openmaptiles", "source-layer": "water", filter: ["!=", ["get", "brunnel"], "tunnel"], paint: { "fill-color": "#9ebdff" } },
    { id: "river", type: "line", source: "openmaptiles", "source-layer": "waterway", paint: { "line-opacity": 0.7 } },
    { id: "park", type: "fill", source: "openmaptiles", paint: {} },
    { id: "city", type: "symbol", source: "openmaptiles", "source-layer": "place", paint: { "icon-opacity": 0.5 } },
    { id: "boundary_state", type: "line", source: "openmaptiles", "source-layer": "boundary", filter: ["==", ["get", "admin_level"], 4], paint: { "line-width": 1, "line-color": "#aaa" } },
    { id: "boundary", type: "line", source: "openmaptiles", "source-layer": "boundary", paint: { "line-opacity": borderOpacity } },
    { id: "road-casing", type: "line", source: "openmaptiles", "source-layer": "transportation", paint: { "line-opacity": 1, "line-width": 0.5 } },
    { id: "highway_motorway_subtle", type: "line", source: "openmaptiles", "source-layer": "transportation", paint: { "line-color": "#181818", "line-width": 2 } },
    { id: "highway_name_motorway", type: "symbol", source: "openmaptiles", "source-layer": "transportation_name" },
  ];
  const paints = new Map<string, unknown>();
  setLayoutProperty.mockImplementation((id: string, _property: string, value: string) => { layout.set(id, value); });
  setPaintProperty.mockImplementation((id: string, property: string, value: unknown) => {
    paints.set(`${id}:${property}`, value);
    const layer = layers.find((item) => item.id === id);
    if (layer !== undefined) layer.paint = { ...layer.paint, [property]: value };
  });
  const addLayer = vi.fn((layer: LayerSpecification, beforeId?: string) => {
    if (beforeId === undefined) layers.push(layer);
    else layers.splice(layers.findIndex((item) => item.id === beforeId), 0, layer);
  });
  const setFilter = vi.fn((id: string, filter: NonNullable<LineLayerSpecification["filter"]>) => {
    const layer = layers.find((item): item is LineLayerSpecification => item.id === id && item.type === "line");
    if (layer !== undefined) layer.filter = filter;
  });
  const map = {
    getSource: () => ({}), getCanvas: () => canvas,
    getStyle: () => ({ layers: structuredClone(layers) }),
    moveLayer: (id: string) => {
      const index = layers.findIndex((item) => item.id === id);
      if (index < 0) throw new Error(`Cannot move missing layer ${id}`);
      const [layer] = layers.splice(index, 1); if (layer !== undefined) layers.push(layer);
    },
    getLayer: (id: string) => layers.find((item) => item.id === id),
    removeLayer: (id: string) => { layers.splice(layers.findIndex((layer) => layer.id === id), 1); },
    getLayoutProperty: (id: string) => layout.get(id),
    getPaintProperty: (id: string, property: string) => paints.get(`${id}:${property}`) ?? (layers.find((layer) => layer.id === id)?.paint as Record<string, unknown>)[property],
    addLayer, setFilter, setLayoutProperty, setPaintProperty,
  } as unknown as MapLibreMap;
  expect(prepareBasemap(map)).toBe("geography-shoreline");
  const order = layers.map((layer) => layer.id);
  expect(order).not.toContain("road-casing");
  expect(order).not.toContain("highway_motorway_subtle");
  expect(order).not.toContain("highway_name_motorway");
  expect(order.indexOf("river")).toBeLessThan(order.indexOf("geography-boundary_state-halo"));
  expect(order.indexOf("geography-boundary-halo")).toBeLessThan(order.indexOf("boundary"));
  expect(order.indexOf("boundary")).toBeLessThan(order.indexOf("city"));
  expect(layers.find((layer) => layer.id === "geography-shoreline")).toMatchObject({
    type: "line", source: "openmaptiles", "source-layer": "water",
    filter: ["all", ["!=", ["get", "brunnel"], "tunnel"], ["==", ["get", "class"], "ocean"]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#aea89c", "line-width": 2.4 },
  });
  expect(order.indexOf("geography-shoreline")).toBeLessThan(order.indexOf("geography-boundary_state-halo"));
  expect(layers.filter((layer) => layer.type === "line" && layer.id.startsWith("geography-") && ["water", "waterway"].includes(layer["source-layer"] ?? "")).map((layer) => layer.id)).toEqual(["geography-shoreline"]);
  expect(layers.find((layer) => layer.id === "geography-boundary-halo")).toMatchObject({ paint: { "line-color": theme === "dark" ? "#101e2d" : "#fffaf3" } });
  const landFilter = ["all", ["==", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]];
  expect(layers.find((layer) => layer.id === "boundary_state")).toMatchObject({ filter: landFilter });
  expect(layers.find((layer) => layer.id === "geography-boundary_state-halo")).toMatchObject({ filter: landFilter });
  expect(layers.find((layer) => layer.id === "boundary")).toMatchObject({ filter: ["all", true, ["!=", ["get", "maritime"], 1]] });
  expect(paints.get("boundary_state:line-width")).toEqual(["interpolate", ["linear"], ["zoom"], 3, 1.25, 8, 1.6, 14, 2]);
  expect(paints.get("background:background-color")).toBe(theme === "dark" ? "#202d38" : undefined);
  expect(paints.get("water:fill-color")).toBe(theme === "dark" ? "#101e2d" : undefined);
  if (theme === "dark") {
    expect(layers.find((layer) => layer.id === "geography-relief")).toMatchObject({ type: "raster", source: "ne2_shaded", paint: { "raster-saturation": -1, "raster-brightness-max": 0.3 } });
    expect(order.indexOf("geography-relief")).toBeLessThan(order.indexOf("water"));
  } else expect(order).not.toContain("geography-relief");
  expect(order.some((id) => id.includes("road") && id.endsWith("-halo"))).toBe(false);
  prepareBasemap(map);
  expect(setFilter).toHaveBeenCalledTimes(2);
  expect(addLayer).toHaveBeenCalledTimes(theme === "dark" ? 4 : 3);
  applyBasemapState(map, { visible: false, opacity: 0.6 });
  expect(paints.get("river:line-opacity")).toBe(0);
  expect(paints.get("geography-boundary-halo:line-opacity")).toEqual(["interpolate", ["linear"], ["zoom"], 0, ["*", 0.4, 0], 4, ["*", 1, 0]]);
  applyBasemapState(map, { visible: true, opacity: 0.6 });
  expect(canvas.style.opacity).toBe("");
  expect(paints.get("river:line-opacity")).toBe(0.42);
  expect(paints.get("city:icon-opacity")).toBe(0.3);
  expect(paints.get("boundary:line-opacity")).toEqual(["interpolate", ["linear"], ["zoom"], 0, ["*", 0.4, 0.6], 4, ["*", 1, 0.6]]);
  applyBasemapState(map, { visible: true, opacity: 1 });
  expect(paints.get("river:line-opacity")).toBe(0.7);
  expect(paints.get("boundary:line-opacity")).toEqual(borderOpacity);
  const changes = setPaintProperty.mock.calls.length;
  applyBasemapState(map, { visible: true, opacity: 1 });
  expect(setPaintProperty).toHaveBeenCalledTimes(changes);
  expect(setLayoutProperty).toHaveBeenLastCalledWith("park", "visibility", "none");
  expect(setLayoutProperty).toHaveBeenCalledTimes(1);
  expect(layers.some((layer) => "source-layer" in layer && ["transportation", "transportation_name"].includes(layer["source-layer"] ?? ""))).toBe(false);
});

it("toggles native labels without changing weather or the style's label opacity", () => {
  const setLayoutProperty = vi.fn();
  const setPaintProperty = vi.fn();
  const layout = new Map<string, string>();
  setLayoutProperty.mockImplementation((id: string, _property: string, value: string) => { layout.set(id, value); });
  const map = {
    getStyle: () => ({ layers: [
      { id: "city", type: "symbol", source: "openmaptiles", "source-layer": "place" },
      { id: "road", type: "symbol", source: "openmaptiles", "source-layer": "transportation_name" },
      { id: "weather", type: "raster", source: "weather" },
    ] }),
    getLayoutProperty: (id: string) => layout.get(id),
    setLayoutProperty, setPaintProperty,
  } as unknown as MapLibreMap;
  applyBasemapLabels(map, { visible: true, opacity: 1 });
  expect(setLayoutProperty).not.toHaveBeenCalled();
  setLayoutProperty.mockClear();
  applyBasemapLabels(map, { visible: false, opacity: 1 });
  expect(setLayoutProperty.mock.calls).toEqual([["city", "visibility", "none"], ["road", "visibility", "none"]]);
  applyBasemapLabels(map, { visible: false, opacity: 0.5 });
  expect(setLayoutProperty).toHaveBeenCalledTimes(2);
  applyBasemapLabels(map, { visible: true, opacity: 1 });
  expect(setLayoutProperty.mock.calls.slice(2)).toEqual([["city", "visibility", "visible"], ["road", "visibility", "visible"]]);
  expect(setPaintProperty).not.toHaveBeenCalled();
});

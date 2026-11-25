// @vitest-environment jsdom
import type { Layer } from "@deck.gl/core";
import { ScatterplotLayer } from "@deck.gl/layers";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap } from "maplibre-gl";
import alertFixture from "@contracts/public/map/alerts.json";
import surfaceFixture from "@contracts/public/map/surface.json";
import { afterEach, expect, it, vi } from "vitest";
import { parseSurfacePlots } from "../../lib/contracts/surface";
import { parseWeatherAlerts } from "../../lib/contracts/weatherAlerts";
import { useAppStore } from "../../stores/appStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useMapStore } from "../../stores/mapStore";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import type { Station } from "../../types/contracts";
import type { SurfacePlot } from "../../types/surface";
import { readCanvasTokens } from "../canvasTokens";
import { useHoverStore } from "../hover/hoverStore";
import { STATIONS_LAYER_ID } from "../layers/StationLayer";
import { SURFACE_LAYER_ID, SURFACE_MARKERS_LAYER_ID, SURFACE_WIND_LAYER_ID, type MarkItem } from "../layers/SurfacePlotLayer";
import { clickFromPick, hoverFromPick, StationScene } from "./stationScene";

const read = cssTokenReader(tokensCss);
const tokens = readCanvasTokens(read);
const regionalPlots = parseSurfacePlots(surfaceFixture).plots;
const freshPlot = regionalPlots[0];
if (freshPlot === undefined) throw new Error("Expected a surface observation fixture");

afterEach(() => {
  useMapStore.setState(useMapStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
  useForecastDrawingStore.setState(useForecastDrawingStore.getInitialState(), true);
  useHoverStore.setState(useHoverStore.getInitialState(), true);
});

function setup() {
  const setProps = vi.fn<(props: { layers: Layer[] }) => void>();
  const scene = new StationScene({ setProps } as unknown as MapLibreOverlay, tokens, read("--font-mono"), 0, vi.fn());
  const render = (map?: MapLibreMap) => {
    if (map !== undefined) scene.updateLabels(map);
    scene.render();
    return setProps.mock.lastCall?.[0].layers ?? [];
  };
  return { scene, render };
}

function marks(layers: Layer[], id = SURFACE_LAYER_ID): readonly MarkItem[] {
  return (layers.find((layer) => layer.id === id)?.props.data ?? []) as readonly MarkItem[];
}

function markerBatch(layers: Layer[]): ScatterplotLayer<SurfacePlot> {
  const layer = layers.find((item): item is ScatterplotLayer<SurfacePlot> =>
    item.id === SURFACE_MARKERS_LAYER_ID && item instanceof ScatterplotLayer,
  );
  if (layer === undefined) throw new Error("Expected the surface marker batch");
  return layer;
}

function pickLayerItem(layers: Layer[], id: string, index: number) {
  const layer = layers.find((item) => item.id === id);
  if (layer === undefined) throw new Error(`Expected layer ${id}`);
  return layer.getPickingInfo({
    info: { layer, index, picked: index >= 0, color: null, x: 20, y: 30, pixelRatio: 1, coordinate: [-95, 32] },
    mode: "hover",
    sourceLayer: layer,
  });
}

it("keeps all global markers on the source array as the camera moves and only projects nearby detail candidates", () => {
  useMapStore.getState().setLayerVisible("surface-plots", true);
  const { scene, render } = setup();
  const fresh = { ...freshPlot, longitude: -98, latitude: 35 };
  const stale = { ...fresh, id: "STALE", longitude: -97.5, stale: true };
  const plots: readonly SurfacePlot[] = Object.freeze([stale, fresh, ...Array.from({ length: 6000 }, (_, index) => ({
    ...fresh,
    id: `S${String(index)}`,
    latitude: -70 + Math.floor(index / 360) * 3,
    longitude: index % 360 - 180,
  }))]);
  scene.setData({ surfacePlots: plots });
  let zoom = 2;
  let center = -97.8;
  const pixelsPerDegree = () => 64 * 2 ** (zoom - 5);
  const project = vi.fn(([lon, lat]: [number, number]) =>
    ({ x: 400 + (lon - center) * pixelsPerDegree(), y: 300 - (lat - 35) * pixelsPerDegree() }),
  );
  const map = {
    getBounds: () => ({ getWest: () => center - 400 / pixelsPerDegree(), getEast: () => center + 400 / pixelsPerDegree(),
      getSouth: () => 35 - 300 / pixelsPerDegree(), getNorth: () => 35 + 300 / pixelsPerDegree() }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }),
    getZoom: () => zoom, getBearing: () => 0, getPitch: () => 0, project,
  } as unknown as MapLibreMap;

  const initial = render(map);
  const markers = markerBatch(initial);
  expect(markers.props).toMatchObject({ visible: true, pickable: true, billboard: true, radiusUnits: "pixels" });
  expect(markers.props.data).toBe(plots);
  expect(pickLayerItem(initial, SURFACE_MARKERS_LAYER_ID, plots.length - 1).object).toBe(plots.at(-1));
  expect(marks(initial)).toHaveLength(0);
  expect(project).toHaveBeenCalledTimes(0);

  zoom = 5;
  const regional = render(map);
  expect(project).toHaveBeenCalledTimes(2);
  expect(new Set(marks(regional).map(({ plot }) => plot))).toEqual(new Set([fresh]));
  expect(marks(regional, SURFACE_WIND_LAYER_ID).map(({ plot }) => plot)).toEqual([fresh]);
  project.mockClear();
  center += 0.05;
  const panned = render(map);
  expect(project).toHaveBeenCalledTimes(2);
  expect(marks(panned)).toBe(marks(regional));
  expect(marks(panned, SURFACE_WIND_LAYER_ID)).toBe(marks(regional, SURFACE_WIND_LAYER_ID));
  project.mockClear();
  render(map);
  expect(project).toHaveBeenCalledTimes(0);

  zoom = 8;
  const close = render(map);
  expect(new Set(marks(close).map(({ plot }) => plot))).toEqual(new Set([fresh, stale]));
  zoom = 4.99;
  project.mockClear();
  const wide = render(map);
  expect(wide.some((layer) => layer.id === SURFACE_LAYER_ID)).toBe(false);
  expect(project).toHaveBeenCalledTimes(0);
  for (const layers of [regional, panned, close, wide]) {
    const batch = markerBatch(layers);
    expect(batch.props.data).toBe(plots);
    expect(batch.props.updateTriggers).toEqual(markers.props.updateTriggers);
    expect(batch.props.getPosition).toBe(markers.props.getPosition);
  }

  const refreshedPlots = [...plots];
  scene.setData({ surfacePlots: refreshedPlots });
  expect(render(map).find((layer) => layer.id === SURFACE_MARKERS_LAYER_ID)?.props.data).toBe(refreshedPlots);
  scene.setData({ surfacePlots: null });
  expect(render().some((layer) => layer.id.startsWith(SURFACE_LAYER_ID))).toBe(false);
});

it("uses the same observation for center, readings, and wind picks alongside upper-air stations and drawing tools", () => {
  useAppStore.getState().setActiveWorkspace("map");
  useMapStore.getState().setLayerVisible("surface-plots", true);
  useMapStore.getState().setLayerOpacity("surface-plots", 0.45);
  const { scene, render } = setup();
  const upperAir: Station = { id: "72357", name: "Norman", source: "FM35", availability: "listed",
    longitude: freshPlot.longitude, latitude: freshPlot.latitude };
  scene.setStations([upperAir]);
  const plots = [freshPlot];
  scene.setData({ surfacePlots: plots });
  const map = {
    getBounds: () => ({ getWest: () => -99, getEast: () => -96, getSouth: () => 34, getNorth: () => 36 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }),
    getZoom: () => 6, getBearing: () => 0, getPitch: () => 0,
    project: () => ({ x: 400, y: 300 }),
  } as unknown as MapLibreMap;
  const layers = render(map);
  const surface = layers.filter((layer) => layer.id.startsWith(SURFACE_LAYER_ID));
  expect(surface.map((layer) => [layer.id, layer.props.pickable])).toEqual([
    [SURFACE_MARKERS_LAYER_ID, true], [SURFACE_LAYER_ID, true], [`${SURFACE_WIND_LAYER_ID}-halo`, false], [SURFACE_WIND_LAYER_ID, true],
  ]);
  expect(surface.every((layer) => layer.props.opacity === 0.45)).toBe(true);
  expect(marks(layers).map(({ mark }) => mark.text)).toEqual([freshPlot.upperText, freshPlot.lowerText, freshPlot.id]);
  expect(pickLayerItem(layers, STATIONS_LAYER_ID, 0).object).toBe(upperAir);
  const stationPick = pickLayerItem(layers, STATIONS_LAYER_ID, 0);
  clickFromPick(stationPick);
  expect(useAppStore.getState().selectedStationId).toBe(upperAir.id);
  for (const id of [SURFACE_MARKERS_LAYER_ID, SURFACE_LAYER_ID, SURFACE_WIND_LAYER_ID]) {
    const pick = pickLayerItem(layers, id, 0);
    hoverFromPick(pick, 0, { left: 10, top: 5 });
    expect(useHoverStore.getState().report).toMatchObject({
      label: freshPlot.label, description: freshPlot.description, x: 30, y: 35,
      observation: { temperatureC: freshPlot.temperatureC, dewpointC: freshPlot.dewpointC },
    });
    clickFromPick(pick);
    expect(useAppStore.getState().selectedStationId).toBeNull();
    expect(useAppStore.getState().selectedPoint).toEqual({ longitude: freshPlot.longitude, latitude: freshPlot.latitude });
    hoverFromPick(pickLayerItem(layers, id, -1), 0, { left: 10, top: 5 });
    expect(useHoverStore.getState().report).toBeNull();
  }

  useForecastDrawingStore.setState({ enabled: true });
  expect(render().some((layer) => layer.id.startsWith(SURFACE_LAYER_ID) || layer.id === STATIONS_LAYER_ID)).toBe(false);
  useForecastDrawingStore.setState({ enabled: false });
  expect(render().find((layer) => layer.id === SURFACE_MARKERS_LAYER_ID)?.props.data).toBe(plots);
  useMapStore.getState().setLayerVisible("surface-plots", false);
  expect(render().find((layer) => layer.id === SURFACE_MARKERS_LAYER_ID)?.props).toMatchObject({ visible: false, data: plots });
});

it("lays out details across the date line and excludes far-side detail candidates while retaining every marker", () => {
  useMapStore.setState({ projection: "globe" });
  useMapStore.getState().setLayerVisible("surface-plots", true);
  const { scene, render } = setup();
  const east = { ...freshPlot, id: "EAST", longitude: 179, latitude: 0 };
  const west = { ...east, id: "WEST", longitude: -179 };
  const back = { ...east, id: "BACK", longitude: 0 };
  const plots = [east, west, back];
  scene.setData({ surfacePlots: plots });
  let bounds = [177, 183];
  const project = vi.fn(([lon]: [number, number]) => ({ x: lon === -179 ? 560 : 240, y: 300 }));
  const unproject = vi.fn(({ x }: { x: number }) => ({ lng: x > 400 ? 181 : 179, lat: 0 }));
  const map = {
    getBounds: () => ({ getWest: () => bounds[0], getEast: () => bounds[1], getSouth: () => -10, getNorth: () => 10 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }),
    getZoom: () => 5, getBearing: () => 0, getPitch: () => 0,
    getCenter: () => ({ lng: 180, lat: 0 }),
    project, unproject,
  } as unknown as MapLibreMap;
  const first = render(map);
  expect(project).toHaveBeenCalledTimes(2);
  expect(new Set(marks(first).map(({ plot }) => plot))).toEqual(new Set([east, west]));
  const markers = first.find((layer) => layer.id === SURFACE_MARKERS_LAYER_ID);
  expect(markers?.props.data).toBe(plots);
  expect(markers?.props.parameters).toMatchObject({ depthCompare: "always", depthWriteEnabled: false });
  expect(markers?.props.extensions).toHaveLength(1);

  bounds = [-180, 180];
  unproject.mockClear();
  const wider = render(map);
  expect(unproject).toHaveBeenCalledTimes(3);
  expect(marks(wider)).toBe(marks(first));
  expect(wider.find((layer) => layer.id === SURFACE_MARKERS_LAYER_ID)?.props.data).toBe(plots);
});

it("retains global surface markers at low zoom and stacks details above Live Tracking alerts when zoomed in", () => {
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setLayerVisible("surface-plots", true);
  useMapStore.getState().setLayerVisible("warnings", true);
  const { scene, render } = setup();
  const plots = regionalPlots.map((plot, index) => ({ ...plot, longitude: -110 + index, latitude: 35 }));
  scene.setData({ surfacePlots: plots, alerts: parseWeatherAlerts(alertFixture).alerts });
  let zoom = 3;
  const map = {
    getBounds: () => ({ getWest: () => -111, getEast: () => -104, getSouth: () => 34, getNorth: () => 36 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }),
    getZoom: () => zoom, getBearing: () => 0, getPitch: () => 0,
    project: ([lon]: [number, number]) => ({ x: 100 + (lon + 110) * 120, y: 150 }),
  } as unknown as MapLibreMap;
  const lowZoom = render(map);
  const lowIds = lowZoom.map((layer) => layer.id);
  expect(lowIds.indexOf("warnings")).toBeGreaterThanOrEqual(0);
  expect(lowIds.indexOf(SURFACE_MARKERS_LAYER_ID)).toBeGreaterThan(lowIds.indexOf("warnings"));
  expect(marks(lowZoom)).toHaveLength(0);

  zoom = 5;
  const layers = render(map);
  const ids = layers.map((layer) => layer.id);
  expect(ids.indexOf("warnings")).toBeGreaterThanOrEqual(0);
  expect(ids.indexOf(SURFACE_MARKERS_LAYER_ID)).toBeGreaterThan(ids.indexOf("warnings"));
  expect(ids.indexOf(SURFACE_LAYER_ID)).toBeGreaterThan(ids.indexOf("warnings"));
  expect(new Set(marks(layers).map(({ plot }) => plot))).toEqual(new Set(plots));
  const glyphs = (id: string) => marks(layers).filter(({ plot }) => plot.id === id).map(({ mark }) => mark.text);
  expect(glyphs("KSWA")).toContain("\u25cc");
  expect(glyphs("KHBR")).toEqual(["\u25cb", "\u00d7", "KHBR"]);
  expect(glyphs("KFOO")).toEqual(["\u25cc", "?", "KFOO"]);
});

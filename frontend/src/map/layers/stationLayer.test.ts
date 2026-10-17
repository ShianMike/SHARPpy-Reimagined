// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap } from "maplibre-gl";
import surfaceFixture from "@contracts/public/map/surface.json";
import { parseSurfacePlots } from "../../lib/contracts/surface";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import type { Station, StationAvailability } from "../../types/contracts";
import { readCanvasTokens } from "../canvasTokens";
import { NO_SCENE_DATA, type LayerBuildContext } from "./registry";
import { buildPointLayers, buildSelectionLayers } from "./SelectionLayer";
import { buildStationLabels, buildStationMarkers } from "./StationLayer";
import { clickFromPick, hoverFromPick, StationScene } from "../runtime/stationScene";
import type { MarkItem } from "./SurfacePlotLayer";
import type { SurfacePlot } from "../../types/surface";
import { useAppStore } from "../../stores/appStore";
import { useHoverStore } from "../hover/hoverStore";
import type { PickingInfo } from "@deck.gl/core";

const read = cssTokenReader(tokensCss);
const tokens = readCanvasTokens(read);

function station(id: string, availability: StationAvailability = "unknown"): Station {
  return { id, name: `Station ${id}`, latitude: 35, longitude: -97, source: "FM35", availability };
}

const stations = [station("72357"), station("72249", "listed"), station("72451", "notListed")];
const visible = { visible: true, opacity: 1 };

function context(overrides: Partial<LayerBuildContext> = {}): LayerBuildContext {
  return {
    ...NO_SCENE_DATA,
    stations,
    labels: null,
    selected: null,
    tokens,
    labelFont: read("--font-mono"),
    placeLabels: [],
    span: 360,
    basemap: visible,
    weatherShown: false,
    ...overrides,
  };
}

type Accessor = (s: Station) => unknown;

describe("station markers", () => {
  it("are pickable, auto-highlighted, and keep the catalogue array", () => {
    const layer = buildStationMarkers(context(), visible);
    expect(layer.id).toBe("stations");
    expect(layer.props.pickable).toBe(true);
    expect(layer.props.autoHighlight).toBe(true);
    expect(layer.props.highlightColor).toEqual([241, 239, 235, 255]); // --map-station-hover #f1efeb
    expect(layer.props.data).toBe(stations);
    expect(layer.props.stroked).toBe(true);
    expect(layer.props.radiusUnits).toBe("pixels");
    expect(layer.props.getRadius).toBe(3.5);
  });

  it("colour each station by availability from the shipped tokens", () => {
    const layer = buildStationMarkers(context(), visible);
    const fill = layer.props.getFillColor as Accessor;
    const edge = layer.props.getLineColor as Accessor;
    expect(fill(station("1", "unknown"))).toEqual([156, 151, 141, 255]); // #9c978d
    expect(fill(station("2", "listed"))).toEqual([224, 48, 48, 255]); // #e03030
    expect(fill(station("3", "notListed"))).toEqual([90, 86, 80, 255]); // #5a5650
    expect(edge(station("1", "unknown"))).toEqual([31, 30, 27, 255]); // #1f1e1b
    expect(edge(station("2", "listed"))).toEqual([122, 20, 20, 255]); // #7a1414
    expect(edge(station("3", "notListed"))).toEqual([19, 19, 18, 255]); // #131312
    const position = layer.props.getPosition as Accessor;
    expect(position(station("4"))).toEqual([-97, 35]);
  });

  it("re-evaluate colours only through stable updateTriggers", () => {
    const first = buildStationMarkers(context(), visible);
    const second = buildStationMarkers(context(), { visible: false, opacity: 0.3 });
    expect(first.props.updateTriggers.getFillColor).toBeDefined();
    expect(second.props.updateTriggers.getFillColor).toEqual(first.props.updateTriggers.getFillColor);
    expect(second.props.updateTriggers.getLineColor).toEqual(first.props.updateTriggers.getLineColor);
    expect(second.props.data).toBe(first.props.data);
    expect(second.props.visible).toBe(false);
    expect(second.props.opacity).toBe(0.3);
  });
});

describe("station labels", () => {
  it("are hidden without a label tier", () => {
    const layer = buildStationLabels(context(), visible);
    expect(layer.id).toBe("station-labels");
    expect(layer.props.visible).toBe(false);
    expect(layer.props.pickable).toBe(true);
  });

  it("keeps station IDs without duplicating the basemap's city names at close zoom", () => {
    const subset = [stations[0] as Station];
    const layer = buildStationLabels(context({ labels: { stations: subset, tier: "name" } }), visible);
    expect(layer.props.visible).toBe(true);
    expect(layer.props.data).toBe(subset);
    const text = layer.props.getText as Accessor;
    expect(text(station("72357"))).toBe("72357");
    expect(layer.props.fontSettings).toMatchObject({ sdf: false });
    expect(layer.props.fontWeight).toBe(600);
    expect(layer.props.outlineWidth).toBe(0);
    expect(layer.props.background).toBe(true);
    expect((layer.props.getBackgroundColor as Accessor)(stations[0] as Station)).toEqual([248, 251, 255, 240]);
    expect(layer.props.getColor).toEqual([37, 53, 74, 255]);
    expect(layer.props.getSize).toBe(15);
    expect(layer.props.getPixelOffset).toEqual([0, -12]);
    expect(layer.props.fontFamily).toContain("sans-serif");
  });

  it("follow the station-labels switch", () => {
    const layer = buildStationLabels(context({ labels: { stations, tier: "id" } }), { visible: false, opacity: 1 });
    expect(layer.props.visible).toBe(false);
  });

  it("lets the label select and inspect its station, and highlights the selected ID", () => {
    const selected = stations[0] as Station;
    const original = useAppStore.getState();
    const info = { layer: { id: "station-labels" }, object: selected, x: 100, y: 50, coordinate: [-97, 35] } as unknown as PickingInfo;
    clickFromPick(info);
    expect(useAppStore.getState().selectedStationId).toBe(selected.id);
    expect(useAppStore.getState().selectedPoint).toBeNull();
    hoverFromPick(info, 0, { left: 20, top: 10 });
    expect(useHoverStore.getState().hovered).toEqual({ stationId: selected.id, x: 120, y: 60 });
    expect(useHoverStore.getState().pointer).toEqual({ panel: 0, longitude: -97, latitude: 35, x: 100, y: 50 });
    const layer = buildStationLabels(context({ selected, labels: { stations, tier: "id" } }), visible);
    expect((layer.props.getBackgroundColor as Accessor)(selected)).toEqual([255, 208, 0, 255]);
    hoverFromPick({ ...info, object: null, picked: false }, 0, { left: 20, top: 10 });
    expect(useHoverStore.getState().hovered).toBeNull();
    useAppStore.setState(original, true);
    useHoverStore.getState().setHovered(null);
  });

  it.each(["stations", "station-labels", "storm-reports", "spc-cat-fill"])("clears null exit picks for %s", (id) => {
    hoverFromPick({ layer: { id }, object: null, x: 0, y: 0 } as unknown as PickingInfo, 0, { left: 0, top: 0 });
    expect(useHoverStore.getState().hovered).toBeNull();
    expect(useHoverStore.getState().outlook).toBeNull();
    expect(useHoverStore.getState().report).toBeNull();
  });
});

it("spaces whole surface plots, prioritizes fresh observations and reveals more when zoomed, without changing readings", () => {
  const base = parseSurfacePlots(surfaceFixture).plots[0];
  if (base === undefined) throw new Error("Missing surface fixture");
  const plots: SurfacePlot[] = [
    { ...base, id: "none", longitude: 0.5, latitude: 0, availability: "unknown" },
    { ...base, id: "stale", longitude: 0.25, latitude: 0, stale: true },
    { ...base, id: "fresh", longitude: 0, latitude: 0 },
    { ...base, id: "outside", longitude: 10, latitude: 0 },
  ];
  const overlay = { setProps: vi.fn<(props: { layers: { id: string; props: { data: MarkItem[] } }[] }) => void>() };
  const scene = new StationScene(overlay as unknown as MapLibreOverlay, tokens, read("--font-mono"), 0, vi.fn());
  let zoom = 100;
  const map = { getBounds: () => ({ getWest: () => -180, getEast: () => 180, getSouth: () => -90, getNorth: () => 90 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 300 }),
    project: ([lon, lat]: [number, number]) => ({ x: 100 + lon * zoom, y: 100 + lat * zoom }) };
  scene.setData({ surfacePlots: plots });
  const drawn = (): SurfacePlot[] => {
    scene.updateLabels(map as unknown as MapLibreMap); scene.render();
    const layers = overlay.setProps.mock.lastCall?.[0]?.layers ?? [];
    return [...new Set(layers.find((layer) => layer.id === "surface-plots")?.props.data.map((item) => item.plot))];
  };
  expect(drawn()).toEqual([plots[2]]);
  zoom = 1000;
  expect(drawn()).toEqual([plots[2], plots[1], plots[0]]);
  expect(plots[2]?.upperText).toBe(base.upperText);
  const saved = useAppStore.getState();
  try {
    hoverFromPick({ layer: { id: "surface-plots" }, object: { plot: plots[2] }, x: 100, y: 50 } as unknown as PickingInfo, 0, { left: 0, top: 0 });
    expect(useHoverStore.getState().report?.observation).toEqual({ temperatureC: base.temperatureC, dewpointC: base.dewpointC });
    clickFromPick({ layer: { id: "surface-plots" }, object: { plot: plots[2] }, coordinate: [1, 1] } as unknown as PickingInfo);
    expect(useAppStore.getState().selectedPoint).toEqual({ longitude: 0, latitude: 0 });
  } finally { useAppStore.setState(saved, true); }
});

describe("selection layer", () => {
  it("is empty and hidden without a selection", () => {
    const layers = buildSelectionLayers(context(), visible);
    expect(layers.map((layer) => layer.id)).toEqual(["selection-halo", "selection-marker"]);
    for (const layer of layers) {
      expect(layer.props.data).toEqual([]);
      expect(layer.props.visible).toBe(false);
    }
  });

  it("draws one point in the selected amber with a white edge", () => {
    const selected = stations[0] as Station;
    const [halo, marker] = buildSelectionLayers(context({ selected }), visible);
    expect(marker?.props.data).toEqual([selected]);
    expect(marker?.props.visible).toBe(true);
    expect(marker?.props.getFillColor).toEqual([255, 208, 0, 255]);
    expect(marker?.props.getLineColor).toEqual([255, 255, 255, 255]);
    expect(halo?.props.getFillColor).toEqual([255, 208, 0, 71]);
    expect(halo?.props.getRadius).toBe(12);
    // The same selection gives the same one-element array.
    const [, again] = buildSelectionLayers(context({ selected }), visible);
    expect(again?.props.data).toBe(marker?.props.data);
  });

  it("draws a selected forecast point with the same marker, and nothing without one", () => {
    expect(buildPointLayers(context(), visible)).toEqual([]);
    const point = { latitude: 35.2, longitude: -97.4 };
    const [marker] = buildPointLayers(context({ point }), visible);
    expect(marker?.props.data).toEqual([point]);
    expect(marker?.props.getFillColor).toEqual([255, 208, 0, 255]);
    expect((marker?.props.getPosition as (p: typeof point) => unknown)(point)).toEqual([-97.4, 35.2]);
  });
});

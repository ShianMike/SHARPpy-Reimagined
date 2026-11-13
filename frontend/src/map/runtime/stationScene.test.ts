// @vitest-environment jsdom
import type { Layer } from "@deck.gl/core";
import { IconLayer, SolidPolygonLayer, TextLayer } from "@deck.gl/layers";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap } from "maplibre-gl";
import stationFixture from "@contracts/public/stations.catalog.full.json";
import type { Station } from "../../types/contracts";
import type { ContourLines } from "../../types/map";
import { afterEach, expect, it, vi } from "vitest";
import outlookFixture from "@contracts/public/map/outlook.cat.json";
import reportsFixture from "@contracts/public/map/reports.json";
import { parseSpcOutlook } from "../../lib/contracts/map";
import { parseStormReports } from "../../lib/contracts/reports";
import { panelLayers, panelWeather, useMapStore } from "../../stores/mapStore";
import { useAppStore } from "../../stores/appStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import { readCanvasTokens } from "../canvasTokens";
import type { PickingInfo } from "@deck.gl/core";
import { useHoverStore } from "../hover/hoverStore";
import { clickFromPick, hoverFromPick, StationScene } from "./stationScene";

afterEach(() => {
  useMapStore.setState(useMapStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
  useBoxSoundingStore.setState(useBoxSoundingStore.getInitialState(), true);
  useHoverStore.setState(useHoverStore.getInitialState(), true);
});

it("selects a clicked radar label on its live panel, syncing capabilities without changing sounding selection", () => {
  useAppStore.getState().selectStation("72357");
  useAppStore.getState().setActiveWorkspace("live");
  const state = useMapStore.getState();
  state.setPanelLayout(2);
  state.setActivePanel(1);
  state.setRadarScope("site"); state.setRadarProduct("velocity"); state.setRadarTilt("2"); state.setLayerVisible("radar", false);
  state.setActivePanel(0);
  const compatible = { id: "KTLX", longitude: -97, latitude: 35, products: ["reflectivity", "velocity"] };
  clickFromPick({ layer: { id: "radar-site-labels" }, object: compatible } as unknown as PickingInfo, 1);
  expect(panelWeather(useMapStore.getState(), 1)).toMatchObject({ radarSite: "KTLX", radarProduct: "velocity", radarTilt: "2" });
  useMapStore.getState().setLayerVisible("radar", false);
  useMapStore.getState().setActivePanel(0);
  const site = { id: "DESS", longitude: 11, latitude: 48, country: "DE", products: ["reflectivity"] };
  const info = { layer: { id: "radar-site-labels" }, object: site, coordinate: [11, 48], x: 12, y: 24 } as unknown as PickingInfo;
  hoverFromPick(info, 1, { left: 10, top: 20 });
  expect(useHoverStore.getState().hovered).toBeNull();
  expect(useHoverStore.getState().pointer).toMatchObject({ panel: 1, longitude: 11, latitude: 48 });
  clickFromPick(info, 1);
  const after = useMapStore.getState();
  expect(after.activePanel).toBe(1);
  expect(panelWeather(after, 1)).toMatchObject({ radarScope: "site", radarSite: "DESS", radarProduct: "reflectivity", radarTilt: "0" });
  expect(panelLayers(after, 1).radar.visible).toBe(true);
  expect(panelWeather(after, 0)).toMatchObject({ radarScope: "mosaic", radarSite: null });
  expect(useAppStore.getState().selectedStationId).toBe("72357");
  clickFromPick({ layer: { id: "stations" }, object: { id: "72469" } } as unknown as PickingInfo, 0);
  expect(useAppStore.getState().selectedStationId).toBe("72469");
  useAppStore.getState().setActiveWorkspace("map");
  clickFromPick(info, 0);
  expect(useAppStore.getState().selectedPoint).toEqual({ longitude: 11, latitude: 48 });
  expect(useMapStore.getState().livePanels[1]?.radarSite).toBe("DESS");
});

it("preserves point and station selection in box mode and restores ordinary point picking", () => {
  useAppStore.getState().selectPoint({ longitude: -97, latitude: 35 });
  useBoxSoundingStore.getState().setMode("box");
  clickFromPick({ coordinate: [-98, 36] } as PickingInfo);
  clickFromPick({ layer: { id: "stations" }, object: { id: "72357" } } as unknown as PickingInfo);
  expect(useAppStore.getState().selectedPoint).toEqual({ longitude: -97, latitude: 35 });
  expect(useAppStore.getState().selectedStationId).toBeNull();
  useBoxSoundingStore.getState().setMode("point");
  clickFromPick({ coordinate: [-98, 36] } as PickingInfo);
  expect(useAppStore.getState().selectedPoint).toEqual({ longitude: -98, latitude: 36 });
});

it("submits independent pressure geometry per panel and removes it from the live scene", () => {
  const pressure: ContourLines = { product: "mslp", runTime: "2026-11-06T00:00:00Z", forecastHour: 6,
    unit: "hPa", interval: 4, segments: [[[-100, 35], [-99, 36]]], labels: [{ position: [-99.5, 35.5], value: 1004 }], wind: null };
  const read = cssTokenReader(tokensCss);
  const submit = vi.fn<(props: { layers: Layer[] }) => void>();
  const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay,
    readCanvasTokens(read), read("--font-mono"), 1, vi.fn());
  useMapStore.getState().setPanelLayout(2);
  useMapStore.getState().setActivePanel(1);
  useMapStore.getState().setLayerVisible("isobars", true);
  useMapStore.getState().setLayerOpacity("isobars", 0.55);
  useMapStore.getState().setLayerVisible("model-field", false);
  expect(scene.setData({ isobars: pressure })).toBe(true);
  expect(scene.setData({ isobars: pressure })).toBe(false);
  scene.render();
  expect(submit.mock.lastCall?.[0].layers.find(({ id }) => id === "isobars")?.props).toMatchObject({ visible: true, opacity: 0.55 });
  expect(scene.shownLayerIds).toContain("isobars");
  expect(scene.shownLayerIds).toContain("isobars-labels");
  const other = new StationScene({ setProps: submit } as unknown as MapLibreOverlay,
    readCanvasTokens(read), read("--font-mono"), 0, vi.fn());
  other.setData({ isobars: pressure }); other.render();
  expect(other.shownLayerIds).not.toContain("isobars");
  useMapStore.setState({ view: "live" });
  scene.render();
  expect(submit.mock.lastCall?.[0].layers.some(({ id }) => id.startsWith("isobars"))).toBe(false);
});

it("reuses unchanged label layouts during four-panel contour playback, invalidating on selection and camera changes", () => {
  const project = vi.fn(([lon, lat]: readonly number[]) => ({ x: ((lon ?? 0) + 100) * 180, y: (40 - (lat ?? 0)) * 180 }));
  let east = -96;
  const map = { getBounds: () => ({ getWest: () => -100, getEast: () => east, getSouth: () => 34, getNorth: () => 40 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }), getZoom: () => 5, getBearing: () => 0, getPitch: () => 0, project } as unknown as MapLibreMap;
  const read = cssTokenReader(tokensCss);
  const scenes = Array.from({ length: 4 }, (_, panel) => {
    const scene = new StationScene({ setProps: vi.fn() } as unknown as MapLibreOverlay, readCanvasTokens(read), read("--font-mono"), panel, vi.fn());
    scene.setStations(stationFixture.stations as Station[]);
    return scene;
  });
  // An initial layout and an unchanged refresh prove reuse for every panel.
  for (let frame = 0; frame < 2; frame++) {
    for (const scene of scenes) {
      scene.setData({ contours: { wind: null, segments: [] } as unknown as ContourLines });
      scene.updateLabels(map);
    }
  }
  expect(project.mock.calls.length).toBe(stationFixture.stations.length * 4);
  project.mockClear();
  useAppStore.getState().selectStation("72357");
  scenes[0]?.updateLabels(map);
  expect(project).toHaveBeenCalled();
  project.mockClear();
  east = -95;
  scenes[0]?.updateLabels(map);
  expect(project).toHaveBeenCalled();
});

it("ignores live point clicks and hides the forecast point until Model Viewer is restored", () => {
  const point = { longitude: -97, latitude: 35 };
  useAppStore.getState().selectPoint(point);
  const submit = vi.fn<(props: { layers: Layer[] }) => void>();
  const read = cssTokenReader(tokensCss);
  const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay,
    readCanvasTokens(read), read("--font-mono"), 0, vi.fn());
  useAppStore.getState().setActiveWorkspace("live");
  clickFromPick({ coordinate: [-98, 36] } as PickingInfo);
  clickFromPick({ layer: { id: "surface-plots" }, object: { plot: { longitude: -99, latitude: 37 } } } as unknown as PickingInfo);
  expect(useAppStore.getState().selectedPoint).toEqual(point);
  scene.render();
  expect(submit.mock.lastCall?.[0].layers.map((layer) => layer.id)).not.toContain("selection-point");
  useAppStore.getState().setActiveWorkspace("map");
  scene.render();
  expect(submit.mock.lastCall?.[0].layers.map((layer) => layer.id)).toContain("selection-point");
  clickFromPick({ coordinate: [-98, 36] } as PickingInfo);
  expect(useAppStore.getState().selectedPoint).toEqual({ longitude: -98, latitude: 36 });
  useAppStore.getState().setActiveWorkspace("live");
  clickFromPick({ layer: { id: "stations" }, object: { id: "72357" } } as unknown as PickingInfo);
  expect(useAppStore.getState().selectedStationId).toBe("72357");
});

it.each(["mercator", "globe"] as const)("keeps annotations readable over terrain without flattening weather geometry in %s", (projection) => {
  useMapStore.setState({ projection });
  useMapStore.getState().setLayerVisible("spc-cat", true);
  useMapStore.getState().setLayerVisible("storm-reports", true);
  const submit = vi.fn<(props: { layers: Layer[] }) => void>();
  const read = cssTokenReader(tokensCss);
  const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay,
    readCanvasTokens(read), read("--font-mono"), 0, vi.fn());
  const outlook = parseSpcOutlook(outlookFixture).outlook;
  if (outlook === null) throw new Error("Missing outlook fixture");
  scene.setData({ outlooks: { cat: outlook }, reports: parseStormReports(reportsFixture).reports });
  scene.render();
  const layers = submit.mock.calls[0]?.[0].layers ?? [];
  const annotations: Layer[] = layers.filter((layer) => layer instanceof TextLayer || layer instanceof IconLayer);
  expect(annotations.map((layer) => layer.id)).toEqual(expect.arrayContaining(["spc-cat-labels", "storm-reports"]));
  for (const layer of annotations) {
    if (projection === "globe") {
      expect(layer.props.parameters).toMatchObject({ cullMode: "none", depthCompare: "always", depthWriteEnabled: false });
    } else expect(layer.props.parameters).not.toHaveProperty("depthCompare", "always");
  }
  const polygons: Layer[] = layers.filter((layer) => layer instanceof SolidPolygonLayer);
  expect(polygons.length).toBeGreaterThan(0);
  for (const layer of polygons) expect(layer.props.parameters).not.toHaveProperty("depthCompare", "always");
});

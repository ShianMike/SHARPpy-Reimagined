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
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import { readCanvasTokens } from "../canvasTokens";
import type { PickingInfo } from "@deck.gl/core";
import { useHoverStore } from "../hover/hoverStore";
import { clickFromPick, hoverFromPick, StationScene } from "./stationScene";
import type { FieldImage } from "../layers/registry";

it("does not capture a field before its enabled overlays resolve and match the frame time", () => {
  const read = cssTokenReader(tokensCss);
  const scene = new StationScene({ setProps: vi.fn() } as unknown as MapLibreOverlay,
    readCanvasTokens(read), read("--font-mono"), 0, vi.fn());
  const request = { model: "hrrr", product: "refc", runTime: "2026-03-10T12:00:00Z", forecastHour: 14 };
  const field = { frame: { ...request, validTime: "2026-03-11T02:00:00Z" } } as unknown as FieldImage;
  scene.setData({ field, captureReady: true, captureTime: "2026-03-11T01:00:00Z" });
  expect(scene.matchesField(request)).toBe(false);
  scene.setData({ captureTime: "2026-03-11T02:00:00.000Z", captureReady: false });
  expect(scene.matchesField(request)).toBe(false);
  scene.setData({ captureReady: true });
  expect(scene.matchesField(request)).toBe(true);
  scene.setData({ previousField: field });
  expect(scene.matchesField(request)).toBe(false);
});

afterEach(() => {
  useMapStore.setState(useMapStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
  useBoxSoundingStore.setState(useBoxSoundingStore.getInitialState(), true);
  useForecastDrawingStore.setState(useForecastDrawingStore.getInitialState(), true);
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

it("excludes station and selection layers from every Draw Forecast panel while preserving map preferences", () => {
  useAppStore.getState().setActiveWorkspace("map");
  const station = stationFixture.stations[0] as Station;
  useAppStore.getState().selectStation(station.id);
  const map = useMapStore.getState();
  map.setPanelLayout(2);
  map.setLayerVisible("station-labels", true);
  map.setActivePanel(1);
  map.setLayerVisible("stations", false);
  const read = cssTokenReader(tokensCss);
  const outlook = parseSpcOutlook(outlookFixture).outlook;
  if (outlook === null) throw new Error("Missing outlook fixture");
  for (const panel of [0, 1]) {
    const submit = vi.fn<(props: { layers: Layer[] }) => void>();
    const onSubmitted = vi.fn();
    const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay,
      readCanvasTokens(read), read("--font-mono"), panel, onSubmitted);
    scene.setStations([station]);
    scene.render();
    expect(scene.shownLayerIds.includes("stations")).toBe(panel === 0);
    expect(scene.shownLayerIds).toContain("selection-marker");

    useForecastDrawingStore.setState({ enabled: true, results: { cat: { areas: outlook.areas, clippedHatches: [], hiddenHatches: [], generatedContours: [] } } });
    scene.render();
    const capturedLayers = submit.mock.lastCall?.[0].layers.map(({ id }) => id) ?? [];
    expect(capturedLayers).toContain("custom-outlook-cat-fill");
    expect(capturedLayers.filter((id) => id === "stations" || id === "station-labels" || id.startsWith("selection-"))).toEqual([]);
    expect(onSubmitted).toHaveBeenLastCalledWith(false);
    expect(panelLayers(useMapStore.getState(), panel).stations.visible).toBe(panel === 0);

    useForecastDrawingStore.setState({ enabled: false });
    scene.render();
    expect(scene.shownLayerIds.includes("stations")).toBe(panel === 0);
    expect(scene.shownLayerIds).toContain("selection-marker");
  }
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
  expect(scene.setData({ isobars: pressure })).toEqual(["isobars"]);
  expect(scene.setData({ isobars: pressure })).toEqual([]);
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

it("reuses settled report captions and reprojects them before a pan finishes", () => {
  useMapStore.setState({ view: "map", projection: "mercator" });
  useMapStore.getState().setLayerVisible("storm-reports", true);
  const [wind, hail] = parseStormReports(reportsFixture).reports;
  if (wind === undefined || hail === undefined) throw new Error("Missing reports");
  const reports = [{ ...wind, longitude: -98.4 }, { ...hail, longitude: -97.2 }];
  let longitude = -98.4;
  let width = 800;
  let projection = "mercator";
  const project = vi.fn(([lon]: readonly number[]) => ({ x: 25 + ((lon ?? 0) - longitude) * 150, y: 180 }));
  const map = {
    isStyleLoaded: () => true, getProjection: () => ({ type: projection }), getTerrain: () => null,
    getCenter: () => ({ lng: longitude, lat: 35 }), getPadding: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
    getBounds: () => ({ getWest: () => -100, getEast: () => -96, getSouth: () => 34, getNorth: () => 40 }),
    getCanvas: () => ({ clientWidth: width, clientHeight: 600 }), getZoom: () => 5,
    getBearing: () => 0, getPitch: () => 0, getRoll: () => 0, getVerticalFieldOfView: () => 37, project,
  } as unknown as MapLibreMap;
  const read = cssTokenReader(tokensCss);
  const submit = vi.fn<(props: { layers: Layer[] }) => void>();
  const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay,
    readCanvasTokens(read), read("--font-mono"), 0, vi.fn());
  const captions = () => submit.mock.lastCall?.[0].layers.find(({ id }) => id === "storm-reports-captions")?.props.data;
  scene.setData({ reports });
  scene.updateLabels(map);
  scene.render();
  const beforePan = captions();
  expect(beforePan).toEqual([reports[1], reports[0]]);

  project.mockClear();
  scene.setData({ fieldMix: 0.5 });
  scene.render();
  expect(project).not.toHaveBeenCalled();
  expect(captions()).toBe(beforePan);

  longitude = -97.9;
  scene.render();
  expect(project).toHaveBeenCalledTimes(reports.length);
  expect(captions()).toEqual([reports[1]]);

  project.mockClear();
  width = 600;
  scene.updateLabels(map);
  scene.render();
  expect(project).toHaveBeenCalledTimes(reports.length);
  expect(captions()).toEqual([reports[1]]);

  project.mockClear();
  scene.setData({ reports: reports.slice(0, 1) });
  scene.render();
  expect(project).toHaveBeenCalledTimes(1);
  expect(captions()).toEqual([]);

  project.mockClear();
  projection = "globe";
  scene.render();
  scene.render();
  expect(project).toHaveBeenCalledTimes(2);
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

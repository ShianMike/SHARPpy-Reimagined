// @vitest-environment jsdom
import type { Layer } from "@deck.gl/core";
import { TextLayer } from "@deck.gl/layers";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap } from "maplibre-gl";
import { afterEach, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import type { RadarSite } from "../../types/map";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import { readCanvasTokens } from "../canvasTokens";
import { StationScene } from "../runtime/stationScene";

afterEach(() => {
  useMapStore.setState(useMapStore.getInitialState(), true);
  useAppStore.setState(useAppStore.getInitialState(), true);
});

it("positions readable site IDs and respects visibility in Live Tracking with either saved model projection", () => {
  const site = { id: "KTLX", longitude: -97.28, latitude: 35.33 };
  const near = { id: "KCLOSE", longitude: -97.27, latitude: 35.34 };
  const outside = { id: "POUT", longitude: 140, latitude: 13 };
  const submit = vi.fn<(props: { layers: Layer[] }) => void>();
  const read = cssTokenReader(tokensCss);
  const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay,
    readCanvasTokens(read), read("--font-mono"), 0, vi.fn());
  const map = {
    getBounds: () => ({ getWest: () => -110, getEast: () => -90, getSouth: () => 30, getNorth: () => 45 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }),
    getZoom: () => 5, getBearing: () => 0, getPitch: () => 0,
    project: ([lon, lat]: [number, number]) => ({ x: (lon + 110) * 40, y: (45 - lat) * 40 }),
  } as unknown as MapLibreMap;
  useAppStore.getState().setActiveWorkspace("live");
  scene.setData({ radarSites: [site, near, outside] });
  scene.updateLabels(map);
  const label = () => submit.mock.lastCall?.[0].layers.find(
    (layer): layer is TextLayer<RadarSite> => layer instanceof TextLayer && layer.id === "radar-site-labels");
  for (const projection of ["mercator", "globe"] as const) {
    useMapStore.setState({ projection });
    scene.render();
    const layer = label();
    expect(layer?.props.data).toEqual([site]);
    expect(layer?.props.visible).toBe(true);
    expect(layer?.props.pickable).toBe(false);
    expect(layer?.props.background).toBe(true);
    expect(layer?.props.getBackgroundColor).toEqual(readCanvasTokens(read).colours.stationLabelFill);
    const station = submit.mock.lastCall?.[0].layers.find(
      (item): item is TextLayer => item instanceof TextLayer && item.id === "station-labels");
    for (const key of ["getColor", "getSize", "getPixelOffset", "getTextAnchor", "getAlignmentBaseline", "fontFamily", "fontWeight", "fontSettings", "getBorderColor", "getBorderWidth", "backgroundPadding", "backgroundBorderRadius"] as const) {
      expect(layer?.props[key]).toEqual(station?.props[key]);
    }
    if (typeof layer?.props.getText !== "function" || typeof layer.props.getPosition !== "function") throw new Error("Missing site accessors");
    const info = { index: 0, data: [site], target: [] };
    expect(layer.props.getText(site, info)).toBe("KTLX");
    expect(layer.props.getPosition(site, info)).toEqual([site.longitude, site.latitude]);
  }
  useMapStore.getState().setLayerVisible("radar-site-labels", false);
  scene.render();
  expect(label()?.props.visible).toBe(false);
  useAppStore.getState().setActiveWorkspace("map");
  scene.render();
  expect(label()).toBeUndefined();
});

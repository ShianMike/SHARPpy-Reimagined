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

it("positions site codes, avoids overlap and respects visibility with either saved model projection", () => {
  const site = { id: "KTLX", name: "Oklahoma City", longitude: -97.28, latitude: 35.33 };
  const near = { id: "0-578-0-nohur", name: "Hurum", longitude: -96.5, latitude: 35.34 };
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
    expect(layer?.props.pickable).toBe(true);
    expect(layer?.props.background).toBe(true);
    expect(layer?.props.getBackgroundColor).toEqual(readCanvasTokens(read).colours.stationLabelFill);
    const station = submit.mock.lastCall?.[0].layers.find(
      (item): item is TextLayer => item instanceof TextLayer && item.id === "station-labels");
    for (const key of ["getColor", "getSize", "getPixelOffset", "getTextAnchor", "getAlignmentBaseline", "fontFamily", "fontWeight", "fontSettings", "backgroundPadding", "backgroundBorderRadius"] as const) {
      expect(layer?.props[key]).toEqual(station?.props[key]);
    }
    if (typeof layer?.props.getText !== "function" || typeof layer.props.getPosition !== "function") throw new Error("Missing site accessors");
    const info = { index: 0, data: [site], target: [] };
    expect(layer.props.getText(site, info)).toBe("KTLX");
    expect(layer.props.getText(near, info)).toBe("NOHUR");
    expect(layer.props.getText(outside, info)).toBe("POUT");
    expect(layer.props.getPosition(site, info)).toEqual([site.longitude, site.latitude]);
  }
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite(near.id);
  scene.updateLabels(map);
  scene.render();
  expect(label()?.props.data).toEqual([near]);
  useMapStore.getState().setLayerVisible("radar-site-labels", false);
  scene.render();
  expect(label()?.props.visible).toBe(false);
  useAppStore.getState().setActiveWorkspace("map");
  scene.render();
  expect(label()).toBeUndefined();
});

it("prioritizes the resolved nearest site as it changes, and stops prioritizing it in mosaic mode", () => {
  const first = { id: "KTLX", longitude: -97.28, latitude: 35.33 };
  const nearest = { id: "KOUN", longitude: -97.2, latitude: 35.33 };
  const submit = vi.fn<(props: { layers: Layer[] }) => void>();
  const read = cssTokenReader(tokensCss), tokens = readCanvasTokens(read);
  const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay, tokens, read("--font-mono"), 0, vi.fn());
  const map = {
    getBounds: () => ({ getWest: () => -110, getEast: () => -90, getSouth: () => 30, getNorth: () => 45 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }), getZoom: () => 5, getBearing: () => 0, getPitch: () => 0,
    project: ([lon, lat]: [number, number]) => ({ x: (lon + 110) * 40, y: (45 - lat) * 40 }),
  } as unknown as MapLibreMap;
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setRadarScope("site");
  scene.setData({ radarSites: [first, nearest], selectedRadarSite: nearest.id });
  const label = () => {
    scene.updateLabels(map);
    scene.render();
    return submit.mock.lastCall?.[0].layers.find((layer): layer is TextLayer<RadarSite> => layer.id === "radar-site-labels");
  };
  const selected = label();
  expect(selected?.props.data).toEqual([nearest]);
  if (typeof selected?.props.getBorderColor !== "function") throw new Error("Missing border accessor");
  expect(selected.props.getBorderColor(nearest, { index: 0, data: [nearest], target: [] })).toEqual(tokens.colours.radarLabelSelectedEdge);
  scene.setData({ selectedRadarSite: first.id });
  expect(label()?.props.data).toEqual([first]);
  scene.setData({ selectedRadarSite: nearest.id });
  expect(label()?.props.data).toEqual([nearest]);
  useMapStore.getState().setRadarScope("mosaic");
  expect(label()?.props.data).toEqual([first]);
  useMapStore.getState().setRadarScope("site");
  expect(label()?.props.data).toEqual([nearest]);
  useMapStore.getState().setRadarSite(first.id);
  expect(label()?.props.data).toEqual([first]);
});

it("shows the active site's green border and gives unavailable red precedence, only selecting in site mode", () => {
  const site = { id: "KTLX", longitude: -97, latitude: 35 };
  const other = { id: "KOUN", longitude: -95, latitude: 35 };
  const read = cssTokenReader(tokensCss), tokens = readCanvasTokens(read);
  const submit = vi.fn<(props: { layers: Layer[] }) => void>();
  const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay, tokens, read("--font-mono"), 0, vi.fn());
  const map = {
    getBounds: () => ({ getWest: () => -110, getEast: () => -90, getSouth: () => 30, getNorth: () => 45 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }), getZoom: () => 5, getBearing: () => 0, getPitch: () => 0,
    project: ([lon, lat]: [number, number]) => ({ x: (lon + 110) * 40, y: (45 - lat) * 40 }),
  } as unknown as MapLibreMap;
  useAppStore.getState().setActiveWorkspace("live");
  scene.setData({ radarSites: [site, other], selectedRadarSite: site.id });
  scene.updateLabels(map);
  const border = (target: RadarSite) => {
    scene.render();
    const layer = submit.mock.lastCall?.[0].layers.find((item): item is TextLayer<RadarSite> => item.id === "radar-site-labels");
    if (typeof layer?.props.getBorderColor !== "function" || typeof layer.props.getBorderWidth !== "function") throw new Error("Missing border accessors");
    const info = { index: 0, data: [site, other], target: [] };
    return { colour: layer.props.getBorderColor(target, info), width: layer.props.getBorderWidth(target, info), triggers: layer.props.updateTriggers };
  };
  expect(border(site)).toMatchObject({ colour: tokens.colours.stationLabelEdge, width: tokens.lengths.stationEdge });
  useMapStore.getState().setRadarScope("site");
  expect(border(site)).toMatchObject({ colour: tokens.colours.radarLabelSelectedEdge, width: tokens.lengths.radarLabelHighlightWidth });
  expect(border(other)).toMatchObject({ colour: tokens.colours.stationLabelEdge, width: tokens.lengths.stationEdge });
  const before = border(site).triggers;
  scene.setData({ unavailableRadarSites: new Set([site.id, other.id]) });
  expect(border(site)).toMatchObject({ colour: tokens.colours.radarLabelUnavailableEdge, width: tokens.lengths.radarLabelHighlightWidth });
  expect(border(other)).toMatchObject({ colour: tokens.colours.radarLabelUnavailableEdge, width: tokens.lengths.radarLabelHighlightWidth });
  expect(border(site).triggers).not.toEqual(before);
  scene.setData({ unavailableRadarSites: new Set() });
  useMapStore.getState().setRadarSite(other.id);
  expect(border(site)).toMatchObject({ colour: tokens.colours.stationLabelEdge, width: tokens.lengths.stationEdge });
  expect(border(other)).toMatchObject({ colour: tokens.colours.radarLabelSelectedEdge, width: tokens.lengths.radarLabelHighlightWidth });
  useMapStore.getState().setRadarScope("mosaic");
  expect(border(other)).toMatchObject({ colour: tokens.colours.stationLabelEdge, width: tokens.lengths.stationEdge });
});

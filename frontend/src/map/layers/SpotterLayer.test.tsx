// @vitest-environment jsdom
import type { PickingInfo } from "@deck.gl/core";
import { IconLayer } from "@deck.gl/layers";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import type { SpotterPosition } from "../../types/spotters";
import { readCanvasTokens } from "../canvasTokens";
import { ReportTooltip } from "../hover/ReportTooltip";
import { useHoverStore } from "../hover/hoverStore";
import { clickFromPick, hoverFromPick } from "../runtime/stationPicking";
import { NO_SCENE_DATA, type LayerBuildContext } from "./registry";
import { buildSpotterLayers, SPOTTER_LAYER_ID } from "./SpotterLayer";
import { STATIONS_LAYER_ID } from "./StationLayer";

const active: SpotterPosition = { longitude: -97.5, latitude: 35.5, updatedAt: "2026-11-20T09:10:27Z", chaseCam: false };
const chaseCam: SpotterPosition = { longitude: -98, latitude: 36, updatedAt: "2026-11-20T09:11:00Z", chaseCam: true,
  displayName: "Taylor Lake", motion: { kind: "heading", degrees: 320 } };
const positions: readonly SpotterPosition[] = [active, chaseCam];
const visible = { visible: true, opacity: 1 };
const context: LayerBuildContext = {
  ...NO_SCENE_DATA,
  spotterPositions: positions,
  tokens: readCanvasTokens(cssTokenReader(tokensCss)),
  stations: [], labels: null, selected: null, labelFont: "monospace", placeLabels: [],
  span: 40, basemap: visible, weatherShown: true,
};

function spotterLayer(ctx = context, state = visible): IconLayer<SpotterPosition> {
  const layers = buildSpotterLayers(ctx, state);
  expect(layers).toHaveLength(1);
  const [layer] = layers;
  if (layer === undefined) throw new Error("Missing spotter layer");
  return layer;
}

function spotterPick(layer: IconLayer<SpotterPosition>, object: SpotterPosition): PickingInfo<SpotterPosition> {
  return { layer, object, x: 100, y: 50, color: null, index: positions.indexOf(object), picked: true, pixelRatio: 1 };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useHoverStore.getState().setInspecting(true);
});

it("batches both marker kinds with a radar halo and keeps the batch inputs stable on camera changes", async () => {
  vi.stubGlobal("Image", class {
    src = "";
    decode() { return Promise.resolve(); }
  });
  const layer = spotterLayer();
  expect(layer).toBeInstanceOf(IconLayer);
  expect(layer.id).toBe(SPOTTER_LAYER_ID);
  expect(layer.props).toMatchObject({ sizeUnits: "pixels", pickable: true, visible: true, opacity: 1 });
  expect(layer.props.data).toBe(positions);
  const { getIcon, getPosition, iconMapping, fetch } = layer.props;
  if (typeof getIcon !== "function" || typeof getPosition !== "function") {
    throw new Error("Missing spotter accessors");
  }
  expect(positions.map((point, index) => getIcon(point, { index, data: positions, target: [] }))).toEqual(["active", "chaseCam"]);
  expect(positions.map((point, index) => getPosition(point, { index, data: positions, target: [] }))).toEqual([[-97.5, 35.5], [-98, 36]]);
  const image: unknown = await fetch("", { propName: "iconAtlas", layer });
  if (!(image instanceof Image)) throw new Error("Missing spotter atlas image");
  const svg = new DOMParser().parseFromString(decodeURIComponent(image.src.slice(image.src.indexOf(",") + 1)), "image/svg+xml");
  expect(svg.querySelector("parsererror")).toBeNull();
  expect(svg.querySelector("g")?.getAttribute("stroke")).toBe("#000000");
  expect(svg.querySelector("circle")?.getAttribute("fill")).toBe("#79b9b5");
  expect(svg.querySelector("path")?.getAttribute("fill")).toBe("#b9a2d3");

  const zoomed = spotterLayer({ ...context, span: 5 }, { visible: false, opacity: 0.4 });
  expect(zoomed.id).toBe(layer.id);
  expect(zoomed.props).toMatchObject({ visible: false, opacity: 0.4 });
  expect(zoomed.props.data).toBe(positions);
  expect(zoomed.props.iconMapping).toBe(iconMapping);
  expect(zoomed.props.fetch).toBe(fetch);
  expect(zoomed.props.getPosition).toBe(getPosition);
  expect(zoomed.props.getIcon).toBe(getIcon);
});

it("omits the batch for an absent or empty snapshot", () => {
  expect(buildSpotterLayers({ ...context, spotterPositions: null }, visible)).toEqual([]);
  expect(buildSpotterLayers({ ...context, spotterPositions: [] }, visible)).toEqual([]);
});

it.each([
  { point: active, label: "Active spotter", update: "2026-11-20 09:10:27 UTC" },
  { point: chaseCam, label: "Chase-cam spotter", update: "2026-11-20 09:11:00 UTC" },
])("inspects $label with the actual UTC update and clears the tooltip", ({ point, label, update }) => {
  const info = spotterPick(spotterLayer(), point);
  const origin = { left: 20, top: 10 };
  render(<ReportTooltip />);

  act(() => { useHoverStore.getState().setInspecting(false); hoverFromPick(info, 1, origin); });
  expect(screen.queryByRole("tooltip")).toBeNull();

  act(() => { useHoverStore.getState().setInspecting(true); hoverFromPick(info, 1, origin); });
  expect(useHoverStore.getState().report).toEqual({
    label, description: `Updated ${update}`, caption: "", x: 120, y: 60,
    source: { kind: "spotter", panel: 1, position: point },
  });
  const tooltip = screen.getByRole("tooltip");
  expect(within(tooltip).getByText(point.displayName ?? label)).toBeTruthy();
  expect(tooltip.querySelector("time")?.dateTime).toBe(point.updatedAt);

  act(() => { hoverFromPick({ ...info, layer: null }, 1, origin); });
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("opens a clickable spotter card, keeps it open when the pointer leaves, and restores focus", () => {
  const app = useAppStore.getState();
  const map = useMapStore.getState();
  const layer = spotterLayer();
  const stations = new IconLayer({ id: STATIONS_LAYER_ID });
  const info = spotterPick(layer, chaseCam);
  render(<><button type="button">Map control</button><ReportTooltip /></>);
  const anchor = screen.getByRole("button", { name: "Map control" });

  try {
    useMapStore.setState({ view: "live" });
    useAppStore.setState({ selectedStationId: null });
    anchor.focus();
    act(() => { clickFromPick(info, 1, { left: 20, top: 10 }); });
    expect(useAppStore.getState().selectedStationId).toBeNull();
    const card = screen.getByRole("dialog", { name: "Spotter details for Taylor Lake" });
    const close = within(card).getByRole("button", { name: "Close spotter details" });
    expect(within(card).getByText("NW 320°")).toBeTruthy();
    expect(document.activeElement).toBe(close);
    act(() => { useHoverStore.getState().clear(true); });
    expect(screen.getByRole("dialog")).toBe(card);
    fireEvent.keyDown(close, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(anchor);

    act(() => { clickFromPick({ ...info, layer: stations, object: { id: "72357" } }); });
    expect(useAppStore.getState().selectedStationId).toBe("72357");
  } finally {
    useAppStore.setState(app, true);
    useMapStore.setState(map, true);
  }
});

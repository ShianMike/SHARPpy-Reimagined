// @vitest-environment jsdom
import { PathLayer } from "@deck.gl/layers";
import type { Map as MapLibreMap } from "maplibre-gl";
import { expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import { readCanvasTokens } from "../canvasTokens";
import type { Ring } from "../layers/outlookGeometry";
import { forecastDrawingLayers } from "./forecastDrawingLayers";
import { wireMapDrawingTools } from "./forecastDrawingRuntime";

const square = (radius: number) => [[-radius, -radius], [radius, -radius], [radius, radius], [-radius, radius]] as const;

it("uses capped segments through the last vertex across the date line and skips coincident samples", ({ onTestFinished }) => {
  onTestFinished(() => { useForecastDrawingStore.setState(useForecastDrawingStore.getInitialState(), true); });
  const start = [178, 42] as const;
  const turn = [179, 41] as const;
  const end = [-179, 42] as const;
  useForecastDrawingStore.setState({ mode: "draw", points: [start, turn, turn, end] });
  const tokens = readCanvasTokens(cssTokenReader(tokensCss));
  const stroke = forecastDrawingLayers(tokens, undefined).find(({ id }) => id === "forecast-drawing-path");
  expect(stroke).toBeInstanceOf(PathLayer);
  expect(stroke?.props).toMatchObject({ capRounded: true, wrapLongitude: true, data: [[start, turn], [turn, [181, 42]]] });
});

it("redraws custom boundaries and keeps the sketch on every drawing panel when outlines are enabled", ({ onTestFinished }) => {
  onTestFinished(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useForecastDrawingStore.setState(useForecastDrawingStore.getInitialState(), true);
    useMapStore.setState(useMapStore.getInitialState(), true);
    useUiStore.setState(useUiStore.getInitialState(), true);
  });
  useAppStore.getState().setActiveWorkspace("map");
  useMapStore.getState().setPanelLayout(2);
  const generated = { sourceId: "wind", level: 5, points: square(5) };
  const risk = square(4);
  const hatch = square(3);
  const hole = square(1);
  const area = { description: "", fill: "#888888", stroke: "#000000", hatch: false, hatchLevel: 0 };
  useForecastDrawingStore.setState({
    enabled: true,
    product: "wind",
    mode: "draw",
    points: risk,
    results: { wind: {
      areas: [
        { ...area, label: "5%", rank: 50, rings: [generated.points] },
        { ...area, label: "15%", rank: 150, rings: [risk] },
        { ...area, label: "CIG2", rank: 10_002, rings: [hatch, hole], hatch: true, hatchLevel: 2 },
      ],
      generatedContours: [generated], clippedHatches: [], hiddenHatches: [],
    } },
  });
  const tokens = readCanvasTokens(cssTokenReader(tokensCss));
  const render = () => forecastDrawingLayers(tokens, undefined);
  const layerIds = () => render().map(({ id }) => id);
  const outlineIds = ["custom-outlook-wind-outline", "forecast-drawing-path", "forecast-drawing-vertices"];
  const filledIds = ["custom-outlook-wind-fill", "custom-outlook-wind-hatch-1", "custom-outlook-wind-hatch-2",
    "custom-outlook-wind-hatch-3", "custom-outlook-wind-outline", "custom-outlook-wind-labels",
    "forecast-drawing-path", "forecast-drawing-vertices"];
  const filled = render();
  expect(filled.map(({ id }) => id)).toEqual(filledIds);
  for (const layer of filled.slice(0, 4)) {
    expect(layer.props.parameters).toMatchObject({ depthWriteEnabled: false });
  }

  const redraws = [0, 1].map((panel) => {
    const canvas = document.createElement("canvas");
    const map = {
      getCanvasContainer: () => canvas,
      getCanvas: () => canvas,
      isStyleLoaded: () => false,
      on: vi.fn(),
      off: vi.fn(),
      doubleClickZoom: { isEnabled: () => false, disable: vi.fn() },
    } as unknown as MapLibreMap;
    const redraw = vi.fn(layerIds);
    onTestFinished(wireMapDrawingTools(map, panel, redraw));
    return redraw;
  });
  const ui = useUiStore.getState();
  ui.setLoop(!ui.loop);
  for (const redraw of redraws) expect(redraw).not.toHaveBeenCalled();
  ui.setForecastOutlinesOnly(true);
  for (const redraw of redraws) expect(redraw).toHaveReturnedWith(outlineIds);

  const [outline] = render();
  const boundaries = outline?.props.data as { path: Ring }[] | undefined;
  expect(boundaries?.map(({ path }) => path)).toEqual([generated.points, risk, hatch, hole].map((ring) => [...ring, ring[0]]));

  ui.setForecastOutlinesOnly(false);
  for (const redraw of redraws) expect(redraw).toHaveLastReturnedWith(filledIds);
});

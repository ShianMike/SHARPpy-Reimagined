import type { Layer } from "@deck.gl/core";
import { PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useUiStore } from "../../stores/uiStore";
import { FORECAST_PRODUCT_LABELS, UNOFFICIAL_FORECAST_TITLE, type ForecastDrawingResult, type ForecastPoint } from "../../types/forecastDrawing";
import type { Outlook } from "../../types/map";
import { wrapLongitude } from "../box/boxGeometry";
import type { LayerBuildContext } from "../layers/registry";
import { drawOutlookLayers } from "../layers/SPCOutlookLayer";
import { deckColour } from "../layers/StationLayer";
import { forecastPreviewPath } from "./forecastDrawingCoordinates";

type OutlookGraphic = Pick<Outlook, "title" | "subtitle" | "areas"> & { labelBands: true };
const graphics = new WeakMap<ForecastDrawingResult, OutlookGraphic>();

export function forecastDrawingLayers(tokens: LayerBuildContext["tokens"], labelLayout: LayerBuildContext["outlookLabelLayout"]): Layer[] {
  const state = useForecastDrawingStore.getState();
  const result = state.results[state.product];
  const layers: Layer[] = [];
  if (result !== undefined && result.areas.length !== 0) {
    let graphic = graphics.get(result);
    if (graphic === undefined) {
      graphic = { title: UNOFFICIAL_FORECAST_TITLE, subtitle: FORECAST_PRODUCT_LABELS[state.product], areas: result.areas, labelBands: true };
      graphics.set(result, graphic);
    }
    const context = labelLayout === undefined ? { tokens } : { tokens, outlookLabelLayout: { ...labelLayout, occupied: [...labelLayout.occupied] } };
    layers.push(...drawOutlookLayers(state.product, graphic, context, { visible: true, opacity: state.mode === "edit" ? 0.45 : 1 }, {
      custom: true,
      outlinesOnly: useUiStore.getState().forecastOutlinesOnly,
    }));
  }
  if (state.mode === "idle" || state.points.length === 0) return layers;
  const path = forecastPreviewPath(state.points, state.mode === "edit");
  // The preview supplies the curves. Round-cap short strokes to avoid extruding a GPU join at draft vertices.
  const segments = path.slice(1).flatMap((end, index): [number, number][][] => {
    const start = path[index];
    if (start === undefined) return [];
    const delta = end[0] - start[0];
    // Keep the short arc when the globe tessellates across the date line.
    const longitude = Math.abs(delta) > 180 ? start[0] + wrapLongitude(delta) : end[0];
    if (start[0] === longitude && start[1] === end[1]) return [];
    return [[start, [longitude, end[1]]]];
  });
  if (segments.length > 0) {
    layers.push(new PathLayer<[number, number][]>({
      id: "forecast-drawing-path",
      data: segments,
      getPath: (segment) => segment,
      getColor: deckColour(tokens.colours.selectedEdge),
      widthUnits: "pixels",
      getWidth: tokens.lengths.domainWidth,
      positionFormat: "XY",
      jointRounded: true,
      capRounded: true,
      wrapLongitude: true,
    }));
  }
  if (state.mode === "draw") {
    layers.push(new ScatterplotLayer<ForecastPoint>({
      id: "forecast-drawing-vertices",
      data: state.points,
      getPosition: (point) => point,
      getFillColor: deckColour(tokens.colours.selected),
      getLineColor: deckColour(tokens.colours.selectedEdge),
      radiusUnits: "pixels",
      getRadius: tokens.lengths.stationRadius,
      lineWidthUnits: "pixels",
      getLineWidth: tokens.lengths.domainWidth,
      stroked: true,
    }));
  }
  return layers;
}

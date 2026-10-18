import { PolygonLayer } from "@deck.gl/layers";
import type { WeatherAlert } from "../../types/weatherAlerts";
import { parseColour } from "../canvasTokens";
import type { LayerBuildContext, LayerState } from "./registry";

export interface AlertShape {
  alert: WeatherAlert;
  polygon: (readonly [number, number])[][];
  colour: [number, number, number, number];
}

const shapes = new WeakMap<readonly WeatherAlert[], Record<WeatherAlert["kind"], AlertShape[]>>();

export function buildAlertLayers(kind: WeatherAlert["kind"], ctx: LayerBuildContext, state: LayerState) {
  const alerts = ctx.alerts;
  if (alerts === null) return [];
  let data = shapes.get(alerts);
  if (data === undefined) {
    data = { warning: [], watch: [] };
    for (const alert of alerts) {
      const [r, g, b] = parseColour(alert.colour, "NWS alert colour");
      for (const polygon of alert.polygons) data[alert.kind].push({ alert, polygon: polygon.map((ring) => [...ring]), colour: [r, g, b, 255] });
    }
    shapes.set(alerts, data);
  }
  return [new PolygonLayer<AlertShape>({
    id: kind === "warning" ? "warnings" : "watches",
    data: data[kind],
    visible: state.visible,
    opacity: state.opacity,
    pickable: true,
    getPolygon: (shape) => shape.polygon,
    getFillColor: ({ colour: [r, g, b] }) => [r, g, b, 35],
    getLineColor: (shape) => shape.colour,
    lineWidthUnits: "pixels",
    getLineWidth: 2,
    lineWidthMinPixels: 2,
  })];
}

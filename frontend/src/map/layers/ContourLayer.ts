// The product's contour lines (REQUEST §7 contours): the backend's isolines
// of the product's own contour field, drawn as thin lines over the colour.
// The lines are a level set of a different record than the coloured one (a
// height field over a wind field), so they come as their own geometry and the
// catalogue says which products carry them and how to draw them.

import { PathLayer } from "@deck.gl/layers";
import type { ContourLines } from "../../types/map";
import { parseColour } from "../canvasTokens";
import type { LayerBuildContext, LayerState } from "./registry";

export const CONTOURS_LAYER_ID = "model-field-contours";

interface Segment {
  path: readonly (readonly [number, number])[];
}

const segments = new WeakMap<ContourLines, Segment[]>();

function segmentData(lines: ContourLines): Segment[] {
  let data = segments.get(lines);
  if (data === undefined) {
    data = lines.segments.map((path) => ({ path }));
    segments.set(lines, data);
  }
  return data;
}

/** The contour lines of the panel's product, over its coloured field. */
export function buildContourLayers(ctx: LayerBuildContext, state: LayerState): PathLayer<Segment>[] {
  const lines = ctx.contours;
  const spec = ctx.contourSpec;
  if (lines === null || spec === null || lines.segments.length === 0) {
    return [];
  }
  return [
    new PathLayer<Segment>({
      id: CONTOURS_LAYER_ID,
      data: segmentData(lines),
      getPath: (segment) => segment.path.map((point): [number, number] => [point[0], point[1]]),
      getColor: parseColour(spec.colour, "contour colour"),
      widthUnits: "pixels",
      getWidth: spec.width,
      capRounded: true,
      jointRounded: true,
      pickable: false,
      visible: state.visible,
      opacity: state.opacity,
    }),
  ];
}

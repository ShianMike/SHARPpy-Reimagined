// The product's contour lines (REQUEST §7 contours): the backend's isolines
// of the product's own contour field, drawn over the colour.
// The lines are a level set of a different record than the coloured one (a
// height field over a wind field), so they come as their own geometry and the
// catalogue says which products carry them and how to draw them.
// Pressure-only frames also carry their H/L centers and the 1000-500 mb
// thickness level set; the dashed thickness splits cold (<= 540 dam) from warm
// air the way the Pivotal MSLP charts do.

import { PathLayer, TextLayer } from "@deck.gl/layers";
import type { PressureCenter, ThicknessContours, ThicknessPath } from "../../types/contours";
import type { ContourLines } from "../../types/map";
import { parseColour } from "../canvasTokens";
import { deckColour } from "./StationLayer";
import type { ContourLabel, ContourPath } from "./contourStrokes";
import { buildContourLabels, dashPaths, drawContours, getPath } from "./contourStrokes";
import type { LayerBuildContext, LayerState } from "./registry";

export const CONTOURS_LAYER_ID = "model-field-contours";
/** The dash geometry every dashed overlay splits its paths with. */
export { dashPaths };

/** The dam level that splits the thickness level set: at or below it the air is cold. */
const THICKNESS_COLD_MAX = 540;
/** Dashed thickness stroke, in CSS pixels. */
const THICKNESS_LINE_WIDTH = 1.5;
/** The H/L glyph and the pressure value stacked above it. */
const CENTER_GLYPH_SIZE = 30;
const CENTER_VALUE_SIZE = 16;
const CENTER_VALUE_OFFSET = (CENTER_GLYPH_SIZE + CENTER_VALUE_SIZE) / 2 + 1;

type ThicknessPartition = { cold: readonly ContourPath[]; warm: readonly ContourPath[] };
const thicknessSplits = new WeakMap<readonly ThicknessPath[], ThicknessPartition>();

/** Cold (<= 540 dam) and warm thickness paths, memoized on the frame's level set. */
function splitThickness(thickness: ThicknessContours): ThicknessPartition {
  const cached = thicknessSplits.get(thickness.paths);
  if (cached !== undefined) return cached;
  const cold: ContourPath[] = [];
  const warm: ContourPath[] = [];
  for (const contour of thickness.paths) {
    (contour.value <= THICKNESS_COLD_MAX ? cold : warm).push(contour.points);
  }
  const partition: ThicknessPartition = { cold, warm };
  thicknessSplits.set(thickness.paths, partition);
  return partition;
}

/**
 * The frame's 1000-500 mb thickness: dashed lines, cold blue at or below 540 dam
 * and warm red above it, with the rounded dam value in the matching colour.
 */
export function buildThicknessContours(
  id: string,
  thickness: ThicknessContours,
  ctx: LayerBuildContext,
  state: LayerState,
  spanBand: number,
): (PathLayer<ContourPath> | TextLayer<ContourLabel>)[] {
  const { colours, lengths } = ctx.tokens;
  const { cold, warm } = splitThickness(thickness);
  const dashedCold = { data: dashPaths(cold, spanBand), colour: deckColour(colours.thicknessCold) };
  const dashedWarm = { data: dashPaths(warm, spanBand), colour: deckColour(colours.thicknessWarm) };
  const line = (slot: string, dashes: typeof dashedCold) => new PathLayer<ContourPath>({
    id: `${id}-thickness-${slot}`,
    data: dashes.data,
    getPath,
    getColor: dashes.colour,
    getWidth: THICKNESS_LINE_WIDTH,
    widthUnits: "pixels",
    antialiasing: true,
    capRounded: true,
    jointRounded: true,
    pickable: false,
    visible: state.visible,
    opacity: state.opacity,
  });
  const lines = [
    ...(dashedCold.data.length > 0 ? [line("cold", dashedCold)] : []),
    ...(dashedWarm.data.length > 0 ? [line("warm", dashedWarm)] : []),
  ];
  const labels = buildContourLabels(`${id}-thickness`, thickness.labels, ctx, state, {
    colour: (label) => label.value <= THICKNESS_COLD_MAX ? colours.thicknessCold : colours.thicknessWarm,
    colourTriggers: [colours.thicknessCold, colours.thicknessWarm],
    formatValue: (value) => String(Math.round(value)),
    size: lengths.labelSize,
    minSpacing: 1, // Use the label bounds to space the supplied thickness anchors.
  });
  return [...lines, ...labels];
}

/**
 * Draw each blue high or red low with a neutral whole-hPa reading above it.
 */
export function buildPressureCenters(
  id: string,
  centers: readonly PressureCenter[],
  ctx: LayerBuildContext,
  state: LayerState,
): TextLayer<PressureCenter>[] {
  if (centers.length === 0) return [];
  const { colours, stationLabelFont } = ctx.tokens;
  const layout = ctx.outlookLabelLayout;
  if (layout !== undefined && state.visible && state.opacity > 0) {
    const padding = 4;
    const top = CENTER_VALUE_OFFSET + CENTER_VALUE_SIZE / 2 + padding;
    const bottom = CENTER_GLYPH_SIZE / 2 + padding;
    for (const center of centers) {
      const { x, y } = layout.project([center.lon, center.lat]);
      const halfWidth = Math.max(CENTER_GLYPH_SIZE, String(center.value).length * CENTER_VALUE_SIZE * 0.7) / 2 + padding;
      if (x + halfWidth >= 0 && x - halfWidth <= layout.width && y + bottom >= 0 && y - top <= layout.height) {
        layout.occupied.push({ x, y: y + (bottom - top) / 2, width: 2 * halfWidth, height: top + bottom });
      }
    }
  }
  const shared = {
    data: centers,
    getPosition: (center: PressureCenter): [number, number] => [center.lon, center.lat],
    sizeUnits: "pixels" as const,
    // The larger SDF atlas keeps the fill and halo distinct in high-resolution captures.
    fontSettings: { sdf: true, fontSize: 64, buffer: 8, radius: 12, smoothing: 0.06 },
    outlineWidth: 5,
    outlineColor: deckColour(colours.labelHalo),
    characterSet: "auto",
    pickable: false,
    visible: state.visible,
    opacity: state.opacity,
  };
  // Both ids end in -labels, so the registry hoists them above every weather fill.
  return [
    new TextLayer<PressureCenter>({
      ...shared,
      id: `${id}-centers-labels`,
      getText: (center) => center.kind,
      getColor: (center) => deckColour(center.kind === "H" ? colours.centerHigh : colours.centerLow),
      updateTriggers: { getColor: [colours.centerHigh, colours.centerLow] },
      getSize: CENTER_GLYPH_SIZE,
      fontFamily: stationLabelFont,
      fontWeight: 800,
    }),
    new TextLayer<PressureCenter>({
      ...shared,
      id: `${id}-center-values-labels`,
      getText: (center) => String(center.value),
      getColor: deckColour(colours.labelText),
      getSize: CENTER_VALUE_SIZE,
      getPixelOffset: [0, -CENTER_VALUE_OFFSET],
      fontFamily: ctx.labelFont,
      fontWeight: 700,
    }),
  ];
}

interface ContourSuppression {
  suppressIsobars?: boolean;
  suppressThickness?: boolean;
}

/**
 * The MSLP level set: smoothed lines with hPa labels, its H/L centers, and the
 * dashed 1000-500 mb thickness. With `suppressIsobars` the MSLP lines, labels,
 * and centers are omitted because the independent Isobars overlay draws them.
 */
export function buildPressureContours(
  id: string,
  lines: ContourLines,
  ctx: LayerBuildContext,
  state: LayerState,
  { suppressIsobars, suppressThickness }: ContourSuppression = {},
): (PathLayer<ContourPath> | TextLayer<ContourLabel> | TextLayer<PressureCenter>)[] {
  // Place the stacks first, then the hPa readings, then the thickness readings.
  const centers = suppressIsobars ? [] : buildPressureCenters(id, lines.centers ?? [], ctx, state);
  const { colours, lengths } = ctx.tokens;
  const isobars = suppressIsobars ? [] : drawContours(id, lines, ctx, state, {
    colour: colours.isobar,
    width: 1.5,
    outline: { colour: colours.isobarHalo, width: 3 },
    labels: {
      colour: colours.isobarLabel,
      halo: colours.isobarLabelHalo,
      haloWidth: 3.5,
      size: lengths.labelSize,
      fontWeight: 700,
      minSpacing: 72, // Keep the existing anchor spacing as the text grows.
    },
  });
  const thickness = suppressThickness || lines.thickness === undefined
    ? [] : buildThicknessContours(id, lines.thickness, ctx, state, ctx.span);
  return [...isobars, ...thickness, ...centers];
}

/** The contour lines of the panel's product, over its coloured field. */
export function buildContourLayers(ctx: LayerBuildContext, state: LayerState) {
  const lines = ctx.contours;
  const spec = ctx.contourSpec;
  if (lines === null || spec === null) return [];
  if (lines.unit === "hPa") {
    const suppressIsobars = ctx.pressureOwnership?.independentIsobars === true;
    // Pressure/wind can share the independent layer's request and frame.
    return buildPressureContours(CONTOURS_LAYER_ID, lines, ctx, state, {
      suppressIsobars,
      suppressThickness: suppressIsobars && lines === ctx.isobars,
    });
  }

  const { colours, lengths } = ctx.tokens;
  if (lines.unit === "dam") {
    const width = Math.max(1.5, spec.width);
    return drawContours(CONTOURS_LAYER_ID, lines, ctx, state, {
      colour: colours.heightContour,
      width,
      outline: { colour: colours.heightContourHalo, width: width + 1.5 },
      labels: { colour: colours.heightContour, halo: colours.heightContourHalo, size: lengths.labelSize },
    });
  }
  return drawContours(CONTOURS_LAYER_ID, lines, ctx, state, {
    colour: parseColour(spec.colour, "contour colour"), width: spec.width,
  });
}

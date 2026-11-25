// The product's contour lines (REQUEST §7 contours): the backend's isolines
// of the product's own contour field, drawn over the colour.
// The lines are a level set of a different record than the coloured one (a
// height field over a wind field), so they come as their own geometry and the
// catalogue says which products carry them and how to draw them.

import { PathLayer, TextLayer } from "@deck.gl/layers";
import type { ContourLines } from "../../types/map";
import { parseColour, type Rgba } from "../canvasTokens";
import { declutterPoints } from "../labels/declutter";
import type { LayerBuildContext, LayerState } from "./registry";

export const CONTOURS_LAYER_ID = "model-field-contours";
type ContourLabel = NonNullable<ContourLines["labels"]>[number];
type ContourPath = NonNullable<ContourLines["paths"]>[number];

const getPath = (path: ContourPath): [number, number][] => path.map(([lon, lat]) => [lon, lat]);

type ContourLabelStyle = { colour?: Rgba; halo?: Rgba; size?: number };

function buildContourLabels(
  id: string, labels: readonly ContourLabel[], ctx: LayerBuildContext, state: LayerState,
  style: ContourLabelStyle = {},
): TextLayer<ContourLabel>[] {
  const { colours, lengths } = ctx.tokens;
  const layout = ctx.outlookLabelLayout;
  const { colour = colours.labelText, halo = colours.labelHalo, size = lengths.placeLabelSize } = style;
  const data = layout === undefined ? labels : declutterPoints(labels, (label) => layout.project(label.position), layout, size * 6)
    .filter((label) => {
      const { x, y } = layout.project(label.position);
      const width = String(label.value).length * size * 0.7 + 8;
      const height = size + 8;
      if (x < width / 2 || x > layout.width - width / 2 || y < height / 2 || y > layout.height - height / 2 ||
        layout.occupied.some((box) => Math.abs(x - box.x) < (width + box.width) / 2 && Math.abs(y - box.y) < (height + box.height) / 2)) return false;
      if (state.visible && state.opacity > 0) layout.occupied.push({ x, y, width, height });
      return true;
    });
  return data.length === 0 ? [] : [new TextLayer<ContourLabel>({
    id: `${id}-labels`,
    data,
    getPosition: (label) => [label.position[0], label.position[1]],
    getText: (label) => String(label.value),
    getColor: [...colour],
    getSize: size,
    sizeUnits: "pixels",
    fontFamily: ctx.labelFont,
    fontWeight: 600,
    fontSettings: { sdf: true, fontSize: 32, buffer: 6, radius: 8, smoothing: 0.06 },
    outlineWidth: lengths.labelHaloWidth,
    outlineColor: [...halo],
    characterSet: "auto",
    pickable: false,
    visible: state.visible,
    opacity: state.opacity,
  })];
}

function drawContours(
  id: string, lines: ContourLines, ctx: LayerBuildContext, state: LayerState,
  style: { colour: Rgba; width: number; outline?: { colour: Rgba; width: number }; labels?: ContourLabelStyle },
): (PathLayer<ContourPath> | TextLayer<ContourLabel>)[] {
  const paths = lines.paths ?? lines.segments;
  if (paths.length === 0) return [];

  const { colour, width, outline, labels } = style;
  const pathProps = {
    data: paths,
    getPath,
    widthUnits: "pixels" as const,
    capRounded: true,
    jointRounded: true,
    pickable: false,
    visible: state.visible,
    opacity: state.opacity,
  };
  return [
    ...(outline ? [new PathLayer<ContourPath>({
      ...pathProps,
      id: `${id}-halo`,
      getColor: outline.colour,
      getWidth: outline.width,
    })] : []),
    new PathLayer<ContourPath>({
      ...pathProps,
      id,
      getColor: colour,
      getWidth: width,
    }),
    ...buildContourLabels(id, lines.labels ?? [], ctx, state, labels),
  ];
}

export function buildPressureContours(id: string, lines: ContourLines, ctx: LayerBuildContext, state: LayerState) {
  const { labelText, labelHalo } = ctx.tokens.colours;
  return drawContours(id, lines, ctx, state, {
    colour: labelText, width: 1.5, outline: { colour: labelHalo, width: 3.5 },
  });
}

/** The contour lines of the panel's product, over its coloured field. */
export function buildContourLayers(ctx: LayerBuildContext, state: LayerState) {
  const lines = ctx.contours;
  const spec = ctx.contourSpec;
  if (lines === null || spec === null) return [];
  if (lines.unit === "hPa") return buildPressureContours(CONTOURS_LAYER_ID, lines, ctx, state);

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

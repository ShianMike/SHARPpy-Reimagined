// Contour stroke and label drawing (REQUEST §7 contours): the shared
// primitives every contour overlay draws with — joined path layers with
// haloes and native labels, and the geometry that splits a joined path into
// dash pieces. The product contour layer composes these; this module owns
// none of the weather meaning.

import { PathLayer, TextLayer } from "@deck.gl/layers";
import type { ContourLines } from "../../types/map";
import type { Rgba } from "../canvasTokens";
import { declutterPoints } from "../labels/declutter";
import type { LayerBuildContext, LayerState } from "./registry";

export type ContourLabel = NonNullable<ContourLines["labels"]>[number];
export type ContourPath = NonNullable<ContourLines["paths"]>[number];
/** A dash of a dashed contour: contour points with no gaps inside the piece. */
export type DashedSegment = ContourPath;

/** Dash and gap as shares of the view's longitude span, in equivalent degrees. */
const DASH_PER_SPAN = 90;
const GAP_PER_SPAN = 130;
const MIN_DASH_STEP = 1e-9;

export const getPath = (path: ContourPath): [number, number][] => path.map(([lon, lat]) => [lon, lat]);

export type ContourLabelStyle = {
  colour?: Rgba | ((label: ContourLabel) => Rgba);
  colourTriggers?: readonly Rgba[];
  formatValue?: (value: number) => string;
  halo?: Rgba;
  haloWidth?: number;
  size?: number;
  fontWeight?: number;
  minSpacing?: number;
};

export function buildContourLabels(
  id: string, labels: readonly ContourLabel[], ctx: LayerBuildContext, state: LayerState,
  style: ContourLabelStyle = {},
): TextLayer<ContourLabel>[] {
  const { colours, lengths } = ctx.tokens;
  const layout = ctx.outlookLabelLayout;
  const {
    colour = colours.labelText,
    colourTriggers,
    formatValue = String,
    halo = colours.labelHalo,
    haloWidth = lengths.labelHaloWidth,
    size = lengths.placeLabelSize,
    fontWeight = 600,
    minSpacing = size * 6,
  } = style;
  const data = layout === undefined ? labels : declutterPoints(
    labels, (label) => layout.project(label.position), layout, minSpacing, (label, { x, y }) => {
      const width = formatValue(label.value).length * size * 0.7 + 8;
      const height = size + 8;
      if (x < width / 2 || x > layout.width - width / 2 || y < height / 2 || y > layout.height - height / 2 ||
        layout.occupied.some((box) => Math.abs(x - box.x) < (width + box.width) / 2 && Math.abs(y - box.y) < (height + box.height) / 2)) return false;
      if (state.visible && state.opacity > 0) layout.occupied.push({ x, y, width, height });
      return true;
    },
  );
  return data.length === 0 ? [] : [new TextLayer<ContourLabel>({
    id: `${id}-labels`,
    data,
    getPosition: (label) => [label.position[0], label.position[1]],
    getText: (label) => formatValue(label.value),
    getColor: typeof colour === "function" ? colour : [...colour],
    ...(colourTriggers === undefined ? {} : { updateTriggers: { getColor: colourTriggers } }),
    getSize: size,
    sizeUnits: "pixels",
    fontFamily: ctx.labelFont,
    fontWeight,
    fontSettings: { sdf: true, fontSize: 32, buffer: 6, radius: 8, smoothing: 0.06 },
    outlineWidth: haloWidth,
    outlineColor: [...halo],
    characterSet: "auto",
    pickable: false,
    visible: state.visible,
    opacity: state.opacity,
  })];
}

export function drawContours(
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
    antialiasing: true,
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

/**
 * The view span in bucketed form, the key the dashes are memoized under. Four
 * buckets per octave keep the drawn dash sizes visually steady through a
 * continuous zoom while bounding the cache.
 */
function dashBand(spanBand: number): number {
  const span = Number.isFinite(spanBand) && spanBand > 0 ? spanBand : 45;
  return 2 ** (Math.round(Math.log2(span) * 4) / 4);
}

/** One path walked as alternating dash and gap pieces, in equivalent degrees. */
function dashPath(points: ContourPath, dash: number, gap: number): DashedSegment[] {
  const out: [number, number][][] = [];
  let drawing = true;
  let rest = dash;
  let current: [number, number][] | null = null;
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1];
    const end = points[index];
    if (start === undefined || end === undefined) continue;
    if (drawing && current === null) current = [[start[0], start[1]]];
    // Equivalent-degree length: longitude scaled to the ground at this latitude.
    const midLatitude = ((start[1] + end[1]) / 2) * Math.PI / 180;
    const lonScale = Math.max(0.05, Math.cos(midLatitude));
    let x = start[0];
    let y = start[1];
    let left = Math.hypot((end[0] - x) * lonScale, end[1] - y);
    while (left > MIN_DASH_STEP) {
      const step = Math.min(rest, left);
      const fraction = step / left;
      x += (end[0] - x) * fraction;
      y += (end[1] - y) * fraction;
      left -= step;
      rest -= step;
      if (drawing && current !== null) current.push([x, y]);
      if (rest > MIN_DASH_STEP) continue;
      if (drawing && current !== null) {
        if (current.length >= 2) out.push(current);
        current = null;
      }
      drawing = !drawing;
      rest = drawing ? dash : gap;
      if (drawing) current = [[x, y]];
    }
  }
  if (drawing && current !== null && current.length >= 2) out.push(current);
  return out;
}

const dashCache = new WeakMap<readonly ContourPath[], Map<number, readonly DashedSegment[]>>();

/**
 * Splits joined geographic paths into dash pieces. Pure geometry, memoized per
 * `paths` reference and span bucket so panning, toggling, and opacity changes
 * reuse the same arrays instead of rebuilding them each frame.
 */
export function dashPaths(paths: readonly ContourPath[], spanBand: number): readonly DashedSegment[] {
  const band = dashBand(spanBand);
  let bands = dashCache.get(paths);
  if (bands === undefined) {
    bands = new Map<number, readonly DashedSegment[]>();
    dashCache.set(paths, bands);
  }
  const cached = bands.get(band);
  if (cached !== undefined) return cached;
  const total = band / DASH_PER_SPAN;
  const gap = band / GAP_PER_SPAN;
  const segments: DashedSegment[] = [];
  for (const path of paths) segments.push(...dashPath(path, total, gap));
  bands.set(band, segments);
  return segments;
}

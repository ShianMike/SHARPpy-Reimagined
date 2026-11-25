// SPC outlook layers (REQUEST §8): one layer group per product (categorical,
// tornado, wind, hail). The backend passes SPC's areas with its colours and
// labels; this module owns the translucent fill, the outline, the hatch over
// significant-severe areas, the labels, hover, and the z-order (by SPC rank).

import { LayerExtension, type Layer } from "@deck.gl/core";
import { PathLayer, SolidPolygonLayer, TextLayer } from "@deck.gl/layers";
import type { Outlook, OutlookArea, OutlookLayerKey } from "../../types/map";
import { parseColour } from "../canvasTokens";
import { bandLabelPoints, closedPaths, labelPoint, type LonLat } from "./outlookGeometry";
import type { LayerBuildContext, LayerState } from "./registry";
import { deckColour } from "./StationLayer";

type Colour = [number, number, number, number];

/** A wash, so the field and the basemap read through (the legacy map's translucent fill). */
const FILL_ALPHA = 0.18;
/** Hatch strokes keep more opacity: they qualify the band beneath (vector.py OVERLAY_HATCH_ALPHA). */
const HATCH_ALPHA = 155;

type OutlookGraphic = Pick<Outlook, "title" | "subtitle" | "areas"> & { labelBands?: boolean };

export interface AreaFill {
  area: OutlookArea;
  outlook: OutlookGraphic;
  polygon: LonLat[][];
  colour: Colour;
}

interface Stroke {
  path: LonLat[];
  colour: Colour;
  hatch: boolean;
}

interface AreaLabel {
  position: LonLat;
  text: string;
}

interface OutlookShapes {
  fills: AreaFill[];
  hatches: [AreaFill[], AreaFill[], AreaFill[]];
  outlines: Stroke[];
  labels: { candidates: LonLat[]; text: string }[];
}

interface OutlookRenderOptions {
  custom?: boolean;
  outlinesOnly?: boolean;
}

function colourOf(hex: string, alpha: number): Colour {
  const [r, g, b] = parseColour(hex, "outlook colour");
  return [r, g, b, alpha];
}

/** Polygon clipping preserves holes; the GPU keeps the hatch size constant at every zoom. */
class IntensityHatchExtension extends LayerExtension<{ hatchLevel: number }> {
  static override extensionName = "IntensityHatch";

  override getShaders(this: Layer, extension: IntensityHatchExtension) {
    return {
      defines: { CIG_HATCH_LEVEL: extension.opts.hatchLevel },
      inject: {
        "vs:#decl": "out float hatchPixelRatio;",
        "vs:#main-end": "hatchPixelRatio = project.devicePixelRatio;",
        "fs:#decl": "in float hatchPixelRatio;",
        "fs:DECKGL_FILTER_COLOR": `
          vec2 p = gl_FragCoord.xy / hatchPixelRatio;
          float distance = abs(mod(p.y - p.x + 8.0, 16.0) - 8.0) * 0.70710678;
          #if CIG_HATCH_LEVEL == 1
            if (mod(p.x + p.y, 32.0) > 16.0) discard;
          #elif CIG_HATCH_LEVEL == 3
            distance = min(distance, abs(mod(p.y + p.x + 8.0, 16.0) - 8.0) * 0.70710678);
          #endif
          float coverage = 1.0 - smoothstep(0.35, 0.85, distance);
          if (coverage <= 0.0) discard;
          color.a *= coverage;
        `,
      },
    };
  }
}

const HATCH_EXTENSIONS = [1, 2, 3].map((hatchLevel) => [new IntensityHatchExtension({ hatchLevel })]);
const cache = new WeakMap<OutlookGraphic, OutlookShapes>();
const LABEL_LATITUDES = [0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9];

/** Built once per outlook object, so a rebuild hands deck.gl the same arrays. */
export function outlookShapes(outlook: OutlookGraphic): OutlookShapes {
  const cached = cache.get(outlook);
  if (cached !== undefined) {
    return cached;
  }
  const shapes: OutlookShapes = { fills: [], hatches: [[], [], []], outlines: [], labels: [] };
  for (const area of [...outlook.areas].sort((a, b) => a.rank - b.rank)) {
    const polygon = area.rings.map((ring) => [...ring]);
    const stroke = colourOf(area.stroke, 255);
    if (area.hatch) {
      // Legacy SIGN keeps the solid diagonal used by CIG2.
      const level = Math.max(1, Math.min(3, area.hatchLevel || 2));
      shapes.hatches[level - 1]?.push({ area, outlook, polygon, colour: colourOf(area.stroke, HATCH_ALPHA) });
    } else if (area.fill !== null) {
      shapes.fills.push({ area, outlook, polygon, colour: colourOf(area.fill, Math.round(FILL_ALPHA * 255)) });
    }
    shapes.outlines.push(...closedPaths(area.rings).map((path) => ({ path, colour: stroke, hatch: area.hatch })));
    if (area.label !== "") {
      const candidates = outlook.labelBands === true && !area.hatch
        ? bandLabelPoints(area.rings, outlook.areas.filter((other) => !other.hatch && other.rank > area.rank).map((other) => other.rings), LABEL_LATITUDES)
        : LABEL_LATITUDES.map((fraction) => labelPoint(area.rings, fraction));
      shapes.labels.push({ candidates, text: area.label });
    }
  }
  cache.set(outlook, shapes);
  return shapes;
}

/** The layer id prefix of a product; hover picks the fill layer by it. */
export function outlookLayerId(key: OutlookLayerKey): string {
  return key === "ero" ? "wpc-ero" : key === "winter" ? "wpc-winter" : `spc-${key}`;
}

export function buildOutlookLayers(key: OutlookLayerKey, ctx: LayerBuildContext, state: LayerState): Layer[] {
  const outlook = ctx.outlooks[key];
  return outlook === undefined ? [] : drawOutlookLayers(key, outlook, ctx, state);
}

export function drawOutlookLayers(key: OutlookLayerKey, outlook: OutlookGraphic,
  ctx: Pick<LayerBuildContext, "tokens" | "outlookLabelLayout">, state: LayerState,
  { custom = false, outlinesOnly = false }: OutlookRenderOptions = {},
): Layer[] {
  const shapes = outlookShapes(outlook);
  const { colours, lengths } = ctx.tokens;
  const hatchStroke = deckColour(colours.labelText);
  const id = custom ? `custom-outlook-${key}` : outlookLayerId(key);
  const shown = { visible: state.visible, opacity: state.opacity };
  const outline = new PathLayer<Stroke>({
    id: `${id}-outline`,
    data: shapes.outlines,
    getPath: (stroke) => stroke.path,
    getColor: (stroke) => custom && stroke.hatch ? hatchStroke : stroke.colour,
    widthUnits: "pixels",
    getWidth: lengths.outlookWidth,
    jointRounded: true,
    ...shown,
  });
  if (custom && outlinesOnly) return [outline];

  // Nested translucent risks blend in rank order. A lower risk's triangles can otherwise occlude
  // pieces of higher risks where the globe meshes meet at different depths.
  const fillStyle = custom ? { ...shown, parameters: { depthWriteEnabled: false } } : shown;
  const hatchInk: Colour = [hatchStroke[0], hatchStroke[1], hatchStroke[2], HATCH_ALPHA];
  const layout = ctx.outlookLabelLayout;
  const labels: AreaLabel[] = [...shapes.labels].reverse().flatMap((label) => {
    const labelText = label.text.replace(/^CIG([123])$/u, "CIG $1");
    const text = custom ? labelText : `${key === "torn" ? "TOR" : key === "ero" ? "ERO" : key === "winter" ? "WSSI" : key.toUpperCase()} ${labelText}`;
    const width = text.length * lengths.outlookLabelSize * 0.7 + 8;
    const height = lengths.outlookLabelSize + 8;
    const position = layout === undefined ? label.candidates[0] : label.candidates.find((candidate) => {
      const { x, y } = layout.project(candidate);
      if (!Number.isFinite(x) || !Number.isFinite(y) || x < width / 2 || x > layout.width - width / 2 || y < height / 2 || y > layout.height - height / 2) return false;
      // shortcut: pairwise checks suit tens of labels; use a spatial index for much denser products.
      if (layout.occupied.some((box) => Math.abs(x - box.x) < (width + box.width) / 2 && Math.abs(y - box.y) < (height + box.height) / 2)) return false;
      if (state.visible && state.opacity > 0) layout.occupied.push({ x, y, width, height });
      return true;
    });
    return position === undefined ? [] : [{ position, text }];
  });
  return [
    new SolidPolygonLayer<AreaFill>({
      id: `${id}-fill`,
      data: shapes.fills,
      getPolygon: (fill) => fill.polygon,
      getFillColor: (fill) => fill.colour,
      pickable: !custom,
      ...fillStyle,
    }),
    ...shapes.hatches.map((data, index) => new SolidPolygonLayer<AreaFill>({
      id: `${id}-hatch-${String(index + 1)}`,
      data,
      extensions: HATCH_EXTENSIONS[index] ?? [],
      getPolygon: (fill) => fill.polygon,
      getFillColor: (fill) => custom ? hatchInk : fill.colour,
      pickable: false,
      ...fillStyle,
    })),
    outline,
    new TextLayer<AreaLabel>({
      id: `${id}-labels`,
      data: labels,
      getPosition: (label) => [label.position[0], label.position[1]],
      getText: (label) => label.text,
      getColor: deckColour(colours.labelText),
      getSize: lengths.outlookLabelSize,
      sizeUnits: "pixels",
      fontFamily: ctx.tokens.stationLabelFont,
      fontWeight: 600,
      fontSettings: { sdf: true, fontSize: 32, buffer: 6, radius: 8, smoothing: 0.06 },
      outlineWidth: lengths.labelHaloWidth * 1.5,
      outlineColor: deckColour(colours.labelHalo),
      characterSet: "auto",
      pickable: false,
      ...shown,
    }),
  ];
}

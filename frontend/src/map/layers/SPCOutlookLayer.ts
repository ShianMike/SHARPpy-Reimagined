// SPC outlook layers (REQUEST §8): one layer group per product (categorical,
// tornado, wind, hail). The backend passes SPC's areas with its colours and
// labels; this module owns the translucent fill, the outline, the hatch over
// significant-severe areas, the labels, hover, and the z-order (by SPC rank).

import { LayerExtension, type Layer } from "@deck.gl/core";
import { PathLayer, SolidPolygonLayer, TextLayer } from "@deck.gl/layers";
import type { Outlook, OutlookArea, OutlookKey } from "../../types/map";
import { parseColour } from "../canvasTokens";
import { closedPaths, labelPoint, type LonLat } from "./outlookGeometry";
import type { LayerBuildContext, LayerState } from "./registry";
import { deckColour } from "./StationLayer";

type Colour = [number, number, number, number];

/** A wash, so the field and the basemap read through (the legacy map's translucent fill). */
const FILL_ALPHA = 0.18;
/** Hatch strokes keep more opacity: they qualify the band beneath (vector.py OVERLAY_HATCH_ALPHA). */
const HATCH_ALPHA = 155;

export interface AreaFill {
  area: OutlookArea;
  outlook: Outlook;
  polygon: LonLat[][];
  colour: Colour;
}

interface Stroke {
  path: LonLat[];
  colour: Colour;
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
const cache = new WeakMap<Outlook, OutlookShapes>();

/** Built once per outlook object, so a rebuild hands deck.gl the same arrays. */
export function outlookShapes(outlook: Outlook): OutlookShapes {
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
    shapes.outlines.push(...closedPaths(area.rings).map((path) => ({ path, colour: stroke })));
    if (area.label !== "") {
      shapes.labels.push({ candidates: [0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9].map((fraction) => labelPoint(area.rings, fraction)), text: area.label });
    }
  }
  cache.set(outlook, shapes);
  return shapes;
}

/** The layer id prefix of a product; hover picks the fill layer by it. */
export function outlookLayerId(key: OutlookKey): string {
  return `spc-${key}`;
}

export function buildOutlookLayers(key: OutlookKey, ctx: LayerBuildContext, state: LayerState): Layer[] {
  const outlook = ctx.outlooks[key];
  if (outlook === undefined) {
    return [];
  }
  const shapes = outlookShapes(outlook);
  const { colours, lengths } = ctx.tokens;
  const id = outlookLayerId(key);
  const shown = { visible: state.visible, opacity: state.opacity };
  const layout = ctx.outlookLabelLayout;
  const labels: AreaLabel[] = [...shapes.labels].reverse().flatMap((label) => {
    const text = `${key === "torn" ? "TOR" : key.toUpperCase()} ${label.text.replace(/^CIG([123])$/, "CIG $1")}`;
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
      pickable: true,
      ...shown,
    }),
    ...shapes.hatches.map((data, index) => new SolidPolygonLayer<AreaFill>({
      id: `${id}-hatch-${String(index + 1)}`,
      data,
      extensions: HATCH_EXTENSIONS[index] ?? [],
      getPolygon: (fill) => fill.polygon,
      getFillColor: (fill) => fill.colour,
      pickable: false,
      ...shown,
    })),
    new PathLayer<Stroke>({
      id: `${id}-outline`,
      data: shapes.outlines,
      getPath: (stroke) => stroke.path,
      getColor: (stroke) => stroke.colour,
      widthUnits: "pixels",
      getWidth: lengths.outlookWidth,
      jointRounded: true,
      ...shown,
    }),
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

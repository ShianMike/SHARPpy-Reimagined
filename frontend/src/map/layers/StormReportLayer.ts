import { IconLayer, TextLayer, type IconLayerProps } from "@deck.gl/layers";
import type { ReportPoint } from "../../types/map";
import { parseColour } from "../canvasTokens";
import { declutterPoints } from "../labels/declutter";
import type { LayerBuildContext, LayerState } from "./registry";
import { deckColour } from "./StationLayer";
import { svgIconAtlas } from "./iconAtlas";

export const REPORTS_LAYER_ID = "storm-reports";
const CAPTION_SPAN_DEG = 16;
export const SYMBOL_PATHS: Readonly<Record<string, string>> = {
  triangle: "M12 4L20 19H4Z",
  circle: "M12 5a7 7 0 1 0 0 14a7 7 0 1 0 0-14Z",
  square: "M5 5H19V19H5Z",
  diamond: "M12 4L20 12L12 20L4 12Z",
};

// Share a 2x atlas across panels; geometric paths keep symbols independent of installed fonts.
export const REPORT_ICONS = (() => {
  const iconMapping: Exclude<IconLayerProps["iconMapping"], string | undefined> = {};
  const paths: string[] = [];
  let x = 0;
  for (const [symbol, path] of Object.entries(SYMBOL_PATHS)) {
    for (const [part, width] of [["halo", 5], ["stroke", 2.5], ["fill", 0]] as const) {
      iconMapping[`${symbol}-${part}`] = { x: x * 2, y: 0, width: 48, height: 48, mask: true };
      paths.push(`<path transform="translate(${String(x)} 0)" d="${path}" fill="white" stroke="white" stroke-width="${String(width)}" stroke-linejoin="round"/>`);
      x += 24;
    }
  }
  return {
    ...svgIconAtlas(`<svg xmlns="http://www.w3.org/2000/svg" width="${String(x * 2)}" height="48" viewBox="0 0 ${String(x)} 24">${paths.join("")}</svg>`),
    iconMapping,
    sizeUnits: "pixels" as const,
    textureParameters: { minFilter: "linear", magFilter: "linear", mipmapFilter: "none" } as const,
  };
})();

function position(report: ReportPoint): [number, number] {
  return [report.longitude, report.latitude];
}

const markerSize = (report: ReportPoint): number => Math.max(20, report.radiusPx * 4 + 8);

type ReportLayout = NonNullable<LayerBuildContext["outlookLabelLayout"]>;
const NO_CAPTIONS: readonly ReportPoint[] = [];
// One entry per panel layout, released with that layout's projection function.
const captionCache = new WeakMap<ReportLayout["project"], {
  view: string; data: readonly ReportPoint[]; labelSize: number; width: number; height: number;
  captions: readonly ReportPoint[];
}>();

function reportCaptions(data: readonly ReportPoint[], ctx: LayerBuildContext): readonly ReportPoint[] {
  if (!(ctx.span <= CAPTION_SPAN_DEG)) return NO_CAPTIONS;
  const layout = ctx.outlookLabelLayout;
  const labelSize = ctx.tokens.lengths.labelSize;
  if (layout === undefined) return data.filter((report) => report.caption !== "");
  const { project, width, height } = layout;
  const view = layout.reportCaptionView?.();
  if (view !== undefined) {
    const cached = captionCache.get(project);
    if (cached?.view === view && cached.data === data && cached.labelSize === labelSize &&
      cached.width === width && cached.height === height) return cached.captions;
  } else {
    captionCache.delete(project);
  }
  const candidates = data.filter((report) => report.caption !== "").reverse();
  const cell = candidates.reduce((size, report) => Math.max(size, report.caption.length * labelSize + markerSize(report)), labelSize * 4);
  const captions = declutterPoints(candidates, (report) => project(position(report)), layout, cell);
  if (view !== undefined) captionCache.set(project, { view, data, labelSize, width, height, captions });
  return captions;
}

export function buildReportLayers(ctx: LayerBuildContext, state: LayerState) {
  const { lengths, colours } = ctx.tokens;
  const data = ctx.reports ?? [];
  if (data.length === 0) return [];
  const markers = (["halo", "stroke", "fill"] as const).map((part) => new IconLayer<ReportPoint>({
    ...REPORT_ICONS,
    id: part === "stroke" ? REPORTS_LAYER_ID : `${REPORTS_LAYER_ID}-${part}`,
    data,
    getPosition: position,
    getIcon: (report) => `${Object.hasOwn(SYMBOL_PATHS, report.symbol) ? report.symbol : "circle"}-${part}`,
    getSize: markerSize,
    getColor: (report): [number, number, number, number] => deckColour(part === "halo" ? colours.labelText
      : parseColour(part === "fill" ? report.fill ?? report.stroke : report.stroke, "report colour")),
    updateTriggers: { getColor: colours },
    pickable: part === "stroke",
    autoHighlight: true,
    highlightColor: deckColour(colours.stationHover),
    visible: state.visible,
    opacity: state.opacity,
  }));
  const captions = reportCaptions(data, ctx);
  return [...markers, new TextLayer<ReportPoint>({
    id: `${REPORTS_LAYER_ID}-captions`,
    data: captions,
    getPosition: position,
    getText: (report) => report.caption,
    getColor: deckColour(colours.labelText),
    getSize: lengths.labelSize,
    sizeUnits: "pixels",
    getPixelOffset: (report) => [markerSize(report) / 2 + lengths.labelHaloWidth * 2, 0],
    getTextAnchor: "start",
    getAlignmentBaseline: "center",
    fontFamily: ctx.tokens.stationLabelFont,
    fontWeight: 600,
    fontSettings: { sdf: true, fontSize: 32, buffer: 6, radius: 8, smoothing: 0.06 },
    outlineWidth: lengths.labelHaloWidth * 1.5,
    outlineColor: deckColour(colours.labelHalo),
    characterSet: "auto",
    pickable: true,
    visible: state.visible && ctx.span <= CAPTION_SPAN_DEG,
    opacity: state.opacity,
  })];
}

// Surface observations keep their provider display slots; wind symbols use
// the same fixed-pixel geometry as model winds instead of font glyphs.

import { IconLayer, TextLayer } from "@deck.gl/layers";
import type { SurfacePlot } from "../../types/surface";
import type { Rgba } from "../canvasTokens";
import { markPlacement, plotMarks, type MarkPlacement, type PlotMark } from "./plotMarks";
import type { LayerBuildContext, LayerState } from "./registry";
import { deckColour } from "./StationLayer";
import { WIND_BARB_ICONS } from "./windBarbIcons";

export const SURFACE_LAYER_ID = "surface-plots";

type Colour = [number, number, number, number];

/** One glyph of one station model, with the plot it belongs to. */
export interface MarkItem {
  plot: SurfacePlot;
  mark: PlotMark;
  placement: MarkPlacement;
}

const markItems = new WeakMap<readonly SurfacePlot[], { text: MarkItem[]; wind: MarkItem[] }>();

function toneColour(ctx: LayerBuildContext, tone: PlotMark["tone"]): Rgba {
  const colours = ctx.tokens.colours;
  switch (tone) {
    case "ink":
      return colours.labelText;
    case "dim":
      return colours.labelText;
    case "warn":
      return colours.plotWarn;
    case "upper":
      return colours.obsUpper;
    default:
      return colours.obsLower;
  }
}

/** Reuse mark buffers until observations change; rotating one complete wind icon preserves its shape. */
export function buildSurfacePlotLayers(
  ctx: LayerBuildContext,
  state: LayerState,
): [] | [TextLayer<MarkItem>, ...IconLayer<MarkItem>[]] {
  const plots = ctx.surfacePlots ?? [];
  if (plots.length === 0) {
    return [];
  }
  let items = markItems.get(plots);
  if (items === undefined) {
    const all = plots.flatMap((plot) =>
      plotMarks(plot).map((mark) => ({ plot, mark, placement: markPlacement(mark) })),
    );
    items = { text: all.filter((item) => item.mark.kind !== "wind"), wind: all.filter((item) => item.mark.kind === "wind") };
    markItems.set(plots, items);
  }
  return [
    new TextLayer<MarkItem>({
      id: SURFACE_LAYER_ID,
      data: items.text,
      getPosition: (item) => [item.plot.longitude, item.plot.latitude],
      getText: (item) => item.mark.text,
      getSize: (item) => item.mark.size,
      getColor: (item): Colour => deckColour(toneColour(ctx, item.mark.tone)),
      updateTriggers: { getColor: ctx.tokens.colours },
      getAngle: (item) => item.placement.angle,
      getPixelOffset: (item) => item.placement.offset,
      getTextAnchor: (item) => (item.mark.kind === "text" ? "start" : "middle"),
      getAlignmentBaseline: (item) => (item.mark.kind === "text" ? "bottom" : "center"),
      sizeUnits: "pixels",
      fontFamily: ctx.tokens.stationLabelFont,
      fontWeight: 600,
      fontSettings: { sdf: true, fontSize: 32, buffer: 6, radius: 8, smoothing: 0.06 },
      outlineWidth: ctx.tokens.lengths.labelHaloWidth * 1.5,
      outlineColor: deckColour(ctx.tokens.colours.labelHalo),
      characterSet: "auto",
      pickable: true,
      autoHighlight: true,
      highlightColor: deckColour(ctx.tokens.colours.stationHover),
      visible: state.visible,
      opacity: state.opacity,
    }),
    ...[true, false].map((halo) => new IconLayer<MarkItem>({
      ...WIND_BARB_ICONS,
      id: `${SURFACE_LAYER_ID}-wind${halo ? "-halo" : ""}`,
      data: items.wind,
      getPosition: (item) => [item.plot.longitude, item.plot.latitude],
      getIcon: (item) => `${item.mark.text}${halo ? "-halo" : ""}`,
      getAngle: (item) => -item.mark.axisDeg,
      getColor: (item): Colour => deckColour(halo ? ctx.tokens.colours.labelHalo : toneColour(ctx, item.mark.tone)),
      updateTriggers: { getColor: ctx.tokens.colours },
      pickable: !halo,
      autoHighlight: true,
      highlightColor: deckColour(ctx.tokens.colours.stationHover),
      visible: state.visible,
      opacity: state.opacity,
    })),
  ];
}

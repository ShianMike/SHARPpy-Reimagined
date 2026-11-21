// Surface observations keep their provider display slots; wind symbols use
// the same fixed-pixel geometry as model winds instead of font glyphs.

import { LayerExtension } from "@deck.gl/core";
import { IconLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { SurfacePlot } from "../../types/surface";
import type { Rgba } from "../canvasTokens";
import { markPlacement, plotMarks, type MarkPlacement, type PlotMark } from "./plotMarks";
import type { LayerBuildContext, LayerState } from "./registry";
import { deckColour } from "./StationLayer";
import { WIND_BARB_ICONS } from "./windBarbIcons";

export const SURFACE_LAYER_ID = "surface-plots";
export const SURFACE_MARKERS_LAYER_ID = `${SURFACE_LAYER_ID}-markers`;
export const SURFACE_WIND_LAYER_ID = `${SURFACE_LAYER_ID}-wind`;

type Colour = [number, number, number, number];

/** One glyph of one station model, with the plot it belongs to. */
export interface MarkItem {
  plot: SurfacePlot;
  mark: PlotMark;
  placement: MarkPlacement;
}

const markItems = new WeakMap<readonly SurfacePlot[], { text: MarkItem[]; wind: MarkItem[] }>();

// The globe overlay has its own depth buffer. Clip screen-facing marks at the
// sphere's horizon in both the colour and picking passes, even while moving.
class SurfaceHorizonExtension extends LayerExtension {
  static override extensionName = "SurfaceHorizon";

  override getShaders() {
    return { inject: { "vs:DECKGL_FILTER_GL_POSITION": `
      if (project.projectionMode == PROJECTION_MODE_GLOBE &&
          dot(geometry.position.xyz, project.cameraPosition - geometry.position.xyz) <= 0.0) {
        position = vec4(0.0, 0.0, 2.0, 1.0);
      }
    ` } };
  }
}

const surfaceExtensions = [new SurfaceHorizonExtension()];

function plotPosition(plot: SurfacePlot): [number, number] {
  return [plot.longitude, plot.latitude];
}

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

/** Reuse glyph data while the detail subset is stable. Each wind barb is one rotated icon. */
export function buildSurfacePlotLayers(ctx: LayerBuildContext, state: LayerState) {
  const { surfacePlots, surfacePresentation, tokens } = ctx;
  const layers: (ScatterplotLayer<SurfacePlot> | TextLayer<MarkItem> | IconLayer<MarkItem>)[] = [];
  if (surfacePlots === null || surfacePlots.length === 0) return layers;
  if (surfacePresentation?.markers) {
    // Keep the original records on the marker layer at every zoom. Details
    // share their centers so the dot and glyphs resolve to the same plot.
    layers.push(new ScatterplotLayer<SurfacePlot>({
      id: SURFACE_MARKERS_LAYER_ID,
      data: surfacePlots,
      getPosition: plotPosition,
      radiusUnits: "pixels",
      getRadius: 2,
      lineWidthUnits: "pixels",
      getLineWidth: tokens.lengths.stationEdge,
      getFillColor: deckColour(tokens.colours.labelText),
      getLineColor: deckColour(tokens.colours.labelHalo),
      stroked: true,
      billboard: true,
      extensions: surfaceExtensions,
      pickable: true,
      autoHighlight: true,
      highlightColor: deckColour(tokens.colours.selected),
      visible: state.visible,
      opacity: state.opacity,
    }));
  }
  const plots = surfacePresentation?.details ?? surfacePlots;
  if (plots.length === 0) return layers;
  let items = markItems.get(plots);
  if (items === undefined) {
    const all = plots.flatMap((plot) =>
      plotMarks(plot).map((mark) => ({ plot, mark, placement: markPlacement(mark) })),
    );
    items = { text: all.filter((item) => item.mark.kind !== "wind"), wind: all.filter((item) => item.mark.kind === "wind") };
    markItems.set(plots, items);
  }
  layers.push(
    new TextLayer<MarkItem>({
      id: SURFACE_LAYER_ID,
      data: items.text,
      getPosition: (item) => plotPosition(item.plot),
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
      extensions: surfaceExtensions,
      pickable: true,
      autoHighlight: true,
      highlightColor: deckColour(ctx.tokens.colours.stationHover),
      visible: state.visible,
      opacity: state.opacity,
    }),
    ...[true, false].map((halo) => new IconLayer<MarkItem>({
      ...WIND_BARB_ICONS,
      id: halo ? `${SURFACE_WIND_LAYER_ID}-halo` : SURFACE_WIND_LAYER_ID,
      data: items.wind,
      getPosition: (item) => plotPosition(item.plot),
      getIcon: (item) => `${item.mark.text}${halo ? "-halo" : ""}`,
      getAngle: (item) => -item.mark.axisDeg,
      getColor: (item): Colour => deckColour(halo ? ctx.tokens.colours.labelHalo : toneColour(ctx, item.mark.tone)),
      updateTriggers: { getColor: ctx.tokens.colours },
      extensions: surfaceExtensions,
      pickable: !halo,
      autoHighlight: true,
      highlightColor: deckColour(ctx.tokens.colours.stationHover),
      visible: state.visible,
      opacity: state.opacity,
    })),
  );
  return layers;
}

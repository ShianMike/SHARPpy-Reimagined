// Mean-sea-level pressure remains its own overlay over any selected model field.
import { buildPressureContours } from "./ContourLayer";
import type { LayerBuildContext, LayerState } from "./registry";

export const ISOBARS_LAYER_ID = "isobars";
export function buildIsobarLayers(ctx: LayerBuildContext, state: LayerState) {
  const lines = ctx.isobars;
  return lines === null ? [] : buildPressureContours(ISOBARS_LAYER_ID, lines, ctx, state);
}

// The selected station (DESIGN-frontend F5): a halo and a marker in their own
// one-point layers, so a selection change never rebuilds the station buffers.

import { ScatterplotLayer } from "@deck.gl/layers";
import type { Station } from "../../types/contracts";
import type { LayerBuildContext, LayerState } from "./registry";
import { deckColour } from "./StationLayer";

export const SELECTION_HALO_LAYER_ID = "selection-halo";
export const SELECTION_MARKER_LAYER_ID = "selection-marker";

const NONE: readonly Station[] = [];
const selections = new WeakMap<Station, readonly Station[]>();

/** One stable one-element array per selected station object. */
function selectionData(selected: Station | null): readonly Station[] {
  if (selected === null) {
    return NONE;
  }
  let data = selections.get(selected);
  if (data === undefined) {
    data = [selected];
    selections.set(selected, data);
  }
  return data;
}

function position(station: Station): [number, number] {
  return [station.longitude, station.latitude];
}

export const POINT_LAYER_ID = "selection-point";

type Point = NonNullable<LayerBuildContext["point"]>;
const points = new WeakMap<Point, readonly Point[]>();

/** The selected forecast point: the same marker, in its own one-point layer. */
export function buildPointLayers(ctx: LayerBuildContext, state: LayerState): ScatterplotLayer<Point>[] {
  const point = ctx.point;
  if (point === null) {
    return [];
  }
  let data = points.get(point);
  if (data === undefined) {
    data = [point];
    points.set(point, data);
  }
  const { colours, lengths } = ctx.tokens;
  return [
    new ScatterplotLayer<Point>({
      id: POINT_LAYER_ID,
      data,
      getPosition: (p) => [p.longitude, p.latitude],
      radiusUnits: "pixels",
      getRadius: lengths.selectedRadius,
      getFillColor: deckColour(colours.selected),
      lineWidthUnits: "pixels",
      getLineWidth: lengths.stationEdge,
      getLineColor: deckColour(colours.selectedEdge),
      stroked: true,
      filled: true,
      pickable: false,
      visible: state.visible,
      opacity: state.opacity,
    }),
  ];
}

export function buildSelectionLayers(ctx: LayerBuildContext, state: LayerState): ScatterplotLayer<Station>[] {
  const { colours, lengths } = ctx.tokens;
  const data = selectionData(ctx.selected);
  const visible = state.visible && data.length > 0;
  return [
    new ScatterplotLayer<Station>({
      id: SELECTION_HALO_LAYER_ID,
      data,
      getPosition: position,
      radiusUnits: "pixels",
      getRadius: lengths.selectedHaloRadius,
      getFillColor: deckColour(colours.selectedHalo),
      stroked: false,
      filled: true,
      pickable: false,
      visible,
      opacity: state.opacity,
    }),
    new ScatterplotLayer<Station>({
      id: SELECTION_MARKER_LAYER_ID,
      data,
      getPosition: position,
      radiusUnits: "pixels",
      getRadius: lengths.selectedRadius,
      getFillColor: deckColour(colours.selected),
      lineWidthUnits: "pixels",
      getLineWidth: lengths.stationEdge,
      getLineColor: deckColour(colours.selectedEdge),
      stroked: true,
      filled: true,
      pickable: false,
      visible,
      opacity: state.opacity,
    }),
  ];
}

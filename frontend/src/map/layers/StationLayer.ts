// Station markers and labels (DESIGN-frontend F5). The marker data is the
// catalogue's own array, so deck.gl keeps the 933-point buffers across rebuilds;
// colour accessors change only through updateTriggers. Labels draw the subset
// chosen by the declutter pass on moveend.

import { ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Rgba } from "../canvasTokens";
import type { Station, StationAvailability } from "../../types/contracts";
import type { LayerBuildContext, LayerState } from "./registry";

export const STATIONS_LAYER_ID = "stations";
export const STATION_LABELS_LAYER_ID = "station-labels";

type Colour = [number, number, number, number];

/** deck.gl takes mutable colour arrays. */
export function deckColour(colour: Rgba): Colour {
  return [colour[0], colour[1], colour[2], colour[3]];
}

export interface AvailabilityColours {
  fill: Readonly<Record<StationAvailability, Colour>>;
  edge: Readonly<Record<StationAvailability, Colour>>;
}

export function availabilityColours(ctx: Pick<LayerBuildContext, "tokens">): AvailabilityColours {
  const c = ctx.tokens.colours;
  return {
    fill: {
      unknown: deckColour(c.stationUnknown),
      listed: deckColour(c.stationListed),
      notListed: deckColour(c.stationNotListed),
    },
    edge: {
      unknown: deckColour(c.stationUnknownEdge),
      listed: deckColour(c.stationListedEdge),
      notListed: deckColour(c.stationNotListedEdge),
    },
  };
}

function position(station: Station): [number, number] {
  return [station.longitude, station.latitude];
}

/** The markers: ScatterplotLayer "stations", pickable, highlighted on the GPU. */
export function buildStationMarkers(ctx: LayerBuildContext, state: LayerState): ScatterplotLayer<Station> {
  const colours = availabilityColours(ctx);
  const { lengths } = ctx.tokens;
  // The accessors read the colour table; the trigger is its content, so a
  // rebuild with the same tokens never re-evaluates 933 colours.
  const colourKey = JSON.stringify(colours);
  return new ScatterplotLayer<Station>({
    id: STATIONS_LAYER_ID,
    data: ctx.stations,
    getPosition: position,
    getFillColor: (station) => colours.fill[station.availability],
    getLineColor: (station) => colours.edge[station.availability],
    radiusUnits: "pixels",
    getRadius: lengths.stationRadius,
    lineWidthUnits: "pixels",
    getLineWidth: lengths.stationEdge,
    stroked: true,
    filled: true,
    pickable: true,
    autoHighlight: true,
    highlightColor: deckColour(ctx.tokens.colours.stationHover),
    visible: state.visible,
    opacity: state.opacity,
    updateTriggers: { getFillColor: colourKey, getLineColor: colourKey },
  });
}

const NO_LABELS: readonly Station[] = [];

/** Quiet, pickable labels; the selected station shares its marker's accent. */
export function buildStationLabels(ctx: LayerBuildContext, state: LayerState): TextLayer<Station> {
  const { colours, lengths } = ctx.tokens;
  return new TextLayer<Station>({
    id: STATION_LABELS_LAYER_ID,
    data: ctx.labels?.stations ?? NO_LABELS,
    getPosition: position,
    getText: (station) => station.id,
    getColor: deckColour(colours.stationLabelText),
    getSize: lengths.labelSize,
    sizeUnits: "pixels",
    getPixelOffset: [0, -lengths.labelOffset],
    getTextAnchor: "middle",
    getAlignmentBaseline: "bottom",
    fontFamily: ctx.tokens.stationLabelFont,
    fontWeight: 600,
    fontSettings: { sdf: false },
    background: true,
    getBackgroundColor: (station) => deckColour(station.id === ctx.selected?.id ? colours.selected : colours.stationLabelFill),
    getBorderColor: deckColour(colours.stationLabelEdge),
    getBorderWidth: lengths.stationEdge,
    backgroundPadding: [lengths.stationRadius * 2, lengths.stationRadius],
    backgroundBorderRadius: lengths.stationRadius * 2,
    characterSet: "auto",
    pickable: true,
    updateTriggers: { getBackgroundColor: [ctx.selected?.id, colours.selected, colours.stationLabelFill] },
    visible: state.visible && ctx.labels !== null,
    opacity: state.opacity,
  });
}

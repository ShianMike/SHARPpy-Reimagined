// Which place names to draw (Phase 2), after the legacy map
// (map_geography.labels_visible_for_span, "standard" density): major cities at
// every span, towns once the view spans at most 30 degrees of longitude. Then
// names a cell apart, in the backend's order, keeping clear of the station
// labels. Pure.

import type { Place } from "../../types/map";
import { declutterPoints, type ScreenPoint, type Viewport } from "./declutter";

/** map_geography.LABEL_SPAN_STANDARD. */
export const TOWN_SPAN_DEG = 30;

export interface PlaceLabelInput {
  places: readonly Place[];
  bounds: { west: number; east: number; south: number; north: number };
  /** The visible longitude span, degrees. */
  span: number;
  project: (place: Place) => ScreenPoint;
  viewport: Viewport;
  /** One name per cell of this size, CSS pixels; names are wide, so it is larger than the station cell. */
  cell: number;
  /** Where station labels are drawn. */
  reserved: readonly ScreenPoint[];
  /** The station label cell, CSS pixels. */
  reservedCell: number;
}

/**
 * Whether a name at `point` would cover the station label at `station`. The
 * name runs right from its place, so it reaches a label up to two station
 * cells to its right, and covers one it starts less than half a cell past.
 * ponytail: names are taken as two station cells wide whatever their length;
 * a long name can still touch a label just beyond. Measure the text with the
 * font's advances if that shows.
 */
function covers(point: ScreenPoint, station: ScreenPoint, cell: number): boolean {
  const dx = point.x - station.x;
  return dx > -2 * cell && dx < cell / 2 && Math.abs(point.y - station.y) < cell / 2;
}

export function selectPlaceLabels(input: PlaceLabelInput): Place[] {
  const { west, east, south, north } = input.bounds;
  const towns = input.span <= TOWN_SPAN_DEG;
  const candidates = input.places.filter((place) => {
    if (
      !(place.tier === 1 || towns) ||
      place.longitude < west ||
      place.longitude > east ||
      place.latitude < south ||
      place.latitude > north
    ) {
      return false;
    }
    const point = input.project(place);
    return input.reserved.every((station) => !covers(point, station, input.reservedCell));
  });
  return declutterPoints(candidates, input.project, input.viewport, input.cell);
}

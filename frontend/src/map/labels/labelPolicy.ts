// Zoom-dependent label tiers (DESIGN-frontend F5). Pure: the visible longitude
// span and the graphics profile give the tier and the declutter cell. High and
// Balanced use the legacy thresholds (ui/maps/station_labels.py:21 and :22);
// Low halves the label work on weak GPUs. The table is profileSettings.labels.

import { profileSettings } from "../../lib/graphics/profileSettings";
import type { LabelTier } from "../../stations/stationFormat";
import type { GraphicsProfile } from "../../types/graphics";

/** Ids when the span is at most idSpanDeg, names too at most nameSpanDeg; else no labels. */
export function labelTierFor(profile: GraphicsProfile, longitudeSpanDeg: number): LabelTier | null {
  const { idSpanDeg, nameSpanDeg } = profileSettings(profile).labels;
  if (!Number.isFinite(longitudeSpanDeg)) {
    return null;
  }
  if (longitudeSpanDeg <= nameSpanDeg) {
    return "name";
  }
  return longitudeSpanDeg <= idSpanDeg ? "id" : null;
}

/** The declutter cell in CSS pixels: one label per cell. */
export function declutterCellFor(profile: GraphicsProfile): number {
  return profileSettings(profile).labels.declutterCell;
}

/** East minus west of the visible bounds, at most a full turn. */
export function longitudeSpan(west: number, east: number): number {
  return Math.min(360, Math.abs(east - west));
}

/**
 * The span a map `fullWidth` wide would show at the zoom of one `width` wide
 * showing `span` (longitude is linear in screen x). The tier thresholds are
 * for the window-wide map, so a two- or four-panel map labels as it would.
 */
export function equivalentSpan(span: number, width: number, fullWidth: number): number {
  return width > 0 && fullWidth > width ? (span * fullWidth) / width : span;
}

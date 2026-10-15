// SoundScopepy's OpenFreeMap styles in the app; local geometry in offline fixtures.

import type { StyleSpecification } from "maplibre-gl";
import type { CanvasTokens } from "../canvasTokens";
import { BASEMAP_FAMILIES, basemapLayers, type BasemapFamily } from "./basemapLayers";

/** Sources watched for startup failures. */
export const BASEMAP_SOURCE_IDS: readonly string[] = import.meta.env.MODE === "fixture" ? BASEMAP_FAMILIES : ["openmaptiles"];

// The original SoundScopepy styles; the offline geometry is only for deterministic fixtures.
export const MAP_STYLE_URLS = {
  light: "https://tiles.openfreemap.org/styles/liberty",
  dark: "https://tiles.openfreemap.org/styles/dark",
} as const;

export const BASEMAP_ATTRIBUTION = "Natural Earth";
const SOURCE_MAX_ZOOM = 8;

/** The GeoJSON of a family; deck.gl draws the same files over the weather layers. */
export function basemapSourceUrl(family: BasemapFamily): string {
  // Fixture builds only: ?scenario=basemap-missing points every source at a
  // file that does not exist, so tests can prove the stations draw without a
  // basemap. Vite folds the condition away in desktop builds.
  const missing =
    import.meta.env.MODE === "fixture" &&
    new URLSearchParams(window.location.search).get("scenario") === "basemap-missing";
  return missing ? `/geo/missing-${family}.geojson` : `/geo/${family}.geojson`;
}

export function basemapStyle(tokens: CanvasTokens): StyleSpecification | string {
  if (import.meta.env.MODE !== "fixture") {
    return MAP_STYLE_URLS[document.documentElement.dataset.theme === "dark" ? "dark" : "light"];
  }
  const sources: StyleSpecification["sources"] = {};
  for (const family of BASEMAP_FAMILIES) {
    sources[family] = {
      type: "geojson",
      data: basemapSourceUrl(family),
      attribution: BASEMAP_ATTRIBUTION,
      maxzoom: SOURCE_MAX_ZOOM,
    };
  }
  return { version: 8, sources, layers: basemapLayers(tokens) };
}

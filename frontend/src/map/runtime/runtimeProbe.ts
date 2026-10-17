// What the fixture-only test hooks read from a map runtime (DESIGN-frontend F4).
// MapLibre types only; loaded with the hooks in fixture builds alone.

import type { Map as MapLibreMap } from "maplibre-gl";
import { useMapStore } from "../../stores/mapStore";
import { BASEMAP_LINE_LAYER_IDS } from "../basemap/basemapLayers";
import type { E2eTarget } from "./e2eHooks";
import type { StationScene } from "./stationScene";

export interface ProbeSource {
  map: MapLibreMap | null;
  scene: StationScene | null;
  container: HTMLElement;
  /** The messages the runtime logged for MapLibre and deck.gl errors. */
  errors: readonly string[];
}

export function createRuntimeProbe(source: () => ProbeSource): E2eTarget {
  return {
    projectStation: (id) => {
      const { map, scene, container } = source();
      const station = scene?.station(id);
      if (map === null || station === undefined) {
        return null;
      }
      const point = map.project([station.longitude, station.latitude]);
      const box = container.getBoundingClientRect();
      return { x: box.left + point.x, y: box.top + point.y };
    },
    labelCount: () =>
      useMapStore.getState().layers["station-labels"].visible ? (source().scene?.labelCount() ?? 0) : 0,
    labelTier: () =>
      useMapStore.getState().layers["station-labels"].visible ? (source().scene?.labelTier() ?? null) : null,
    camera: () => {
      const { map } = source();
      if (map === null) {
        return null;
      }
      const center = map.getCenter();
      return { center: [center.lng, center.lat], zoom: map.getZoom() };
    },
    basemapFeatureCounts: () => {
      const { map } = source();
      const counts: Record<string, number> = {};
      for (const id of BASEMAP_LINE_LAYER_IDS) {
        counts[id] = map === null || map.getLayer(id) === undefined ? 0 : map.queryRenderedFeatures({ layers: [id] }).length;
      }
      return counts;
    },
    projection: () => {
      const type = source().map?.getProjection().type;
      return typeof type === "string" ? type : null;
    },
    mapErrors: () => [...source().errors],
    deckLayers: () => [...(source().scene?.shownLayerIds ?? [])],
    projectPoint: (longitude, latitude) => {
      const { map, container } = source();
      if (map === null) {
        return null;
      }
      const point = map.project([longitude, latitude]);
      const box = container.getBoundingClientRect();
      return { x: box.left + point.x, y: box.top + point.y };
    },
  };
}

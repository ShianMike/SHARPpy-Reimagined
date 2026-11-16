import { useEffect, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { captureSoundingMap, mapContextMatchesSounding, soundingMapContext, soundingMapRevision, subscribeSoundingMapContext, type SoundingMapSnapshot } from "../../map/runtime/soundingLocator";
import { useForecastStore } from "../../stores/forecastStore";
import { useAppStore } from "../../stores/appStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { formatCoordinates } from "../../stations/stationFormat";
import { useUiStore } from "../../stores/uiStore";
import type { SoundingView } from "../../types/soundingView";
import { composeSoundingLocator } from "./soundingLocatorImage";

/** Capture only an already displayed, matching forecast; the sounding itself never waits on map I/O. */
export function useSoundingLocator(id: string, timeIndex: number, view: SoundingView | undefined) {
  const summary = useSoundingStore((state) => state.open.find((item) => item.soundingId === id));
  const panel = useMapStore((state) => state.activePanel);
  const mapView = useMapStore((state) => state.view);
  const projection = useMapStore((state) => state.projection);
  const layers = useMapStore((state) => panelLayers(state, panel));
  const layerOrder = useMapStore((state) => state.layerOrder);
  const camera = useMapStore((state) => state.cameraSnapshot);
  const point = useAppStore((state) => state.selectedPoint);
  const station = useAppStore((state) => state.selectedStationId);
  const exporting = useForecastStore((state) => state.exporting);
  const context = useSyncExternalStore(subscribeSoundingMapContext, () => soundingMapContext(panel), () => null);
  const units = useUiStore((state) => state.units);
  const bounds = view?.locatorBounds;
  const location = summary?.stationName?.trim() || bounds?.location || (
    summary?.latitude != null && summary.longitude != null
      ? formatCoordinates({ latitude: summary.latitude, longitude: summary.longitude }) : ""
  );
  const size = bounds === undefined || view === undefined ? undefined : {
    width: Math.ceil((bounds.right - bounds.left) * view.width),
    height: Math.ceil((bounds.bottom - bounds.top) * view.height),
  };
  const enabled = size !== undefined && summary?.latitude != null && summary.longitude != null
    && mapView === "map" && !exporting && context !== null
    && mapContextMatchesSounding(context, summary, timeIndex);
  const snapshot = useQuery({
    queryKey: ["soundings", "locator", id, timeIndex, soundingMapRevision(context), panel, projection, camera, layers, layerOrder, point, station, size],
    enabled,
    retry: false,
    staleTime: Infinity,
    refetchOnMount: "always",
    gcTime: 5 * 60_000,
    queryFn: ({ signal }) => {
      if (size === undefined || context === null || summary?.latitude == null || summary.longitude == null) return null;
      return captureSoundingMap(context, summary.latitude, summary.longitude, size, AbortSignal.any([signal, AbortSignal.timeout(8_000)]));
    },
  });
  const [composed, setComposed] = useState<{ view: SoundingView; snapshot: SoundingMapSnapshot; units: typeof units; location: string; result: SoundingView } | null>(null);
  const current = enabled && !snapshot.isFetching ? snapshot.data ?? null : null;
  useEffect(() => {
    if (view === undefined || current === null) return;
    let active = true;
    void composeSoundingLocator(view, current, units, location).then((result) => {
      if (active) setComposed({ view, snapshot: current, units, location, result });
    }, (error: unknown) => {
      console.warn("[sounding] locator image could not be composed", error);
      if (active) setComposed({ view, snapshot: current, units, location, result: view });
    });
    return () => { active = false; };
  }, [view, current, units, location]);
  const finished = view !== undefined && current !== null && composed?.view === view && composed.snapshot === current
    && composed.units === units && composed.location === location;
  return { view: finished ? composed.result : view, pending: enabled && (snapshot.isFetching || current !== null && !finished) };
}

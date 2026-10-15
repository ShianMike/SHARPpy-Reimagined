// Station lookup by id (DESIGN-frontend F3). The index is memoized per
// catalogue array, so lookups are cheap and refetches build a new index.

import type { Station } from "../types/contracts";
import { useAppStore } from "../stores/appStore";
import { useStationCatalog } from "./stationQueries";

const indexes = new WeakMap<readonly Station[], ReadonlyMap<string, Station>>();

export function stationIndex(stations: readonly Station[]): ReadonlyMap<string, Station> {
  let index = indexes.get(stations);
  if (index === undefined) {
    index = new Map(stations.map((station) => [station.id, station]));
    indexes.set(stations, index);
  }
  return index;
}

export type SelectedStation =
  | { state: "none" }
  /** The catalogue is loading or failed: nothing is shown for the id yet. */
  | { state: "waiting"; id: string }
  | { state: "found"; id: string; station: Station }
  /** A later refetch removed an id the user had selected. */
  | { state: "missing"; id: string };

export function resolveSelection(id: string | null, stations: readonly Station[] | undefined): SelectedStation {
  if (id === null) {
    return { state: "none" };
  }
  if (stations === undefined) {
    return { state: "waiting", id };
  }
  const station = stationIndex(stations).get(id);
  return station === undefined ? { state: "missing", id } : { state: "found", id, station };
}

/** The selected station, looked up in the current catalogue. */
export function useSelectedStation(): SelectedStation {
  const id = useAppStore((state) => state.selectedStationId);
  const catalog = useStationCatalog();
  return resolveSelection(id, catalog.data?.stations);
}

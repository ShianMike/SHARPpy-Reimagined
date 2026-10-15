// One camera for every map panel (REQUEST §14): a move on any linked map is
// copied to the others as it happens. The flag stops the copies' own move
// events from echoing back. All panels settle their labels only when the
// originating gesture ends; jumpTo's synthetic moveend is not a finished gesture.

import type { Map as MapLibreMap } from "maplibre-gl";

const linked = new Map<MapLibreMap, () => void>();
let copying = false;

function copyCamera(from: MapLibreMap, to: MapLibreMap): void {
  const center = from.getCenter();
  const target = to.getCenter();
  const zoom = from.getZoom();
  if (center.lng !== target.lng || center.lat !== target.lat || zoom !== to.getZoom()) {
    to.jumpTo({ center, zoom });
  }
}

/** Links a map; returns the function that unlinks it. */
export function linkCamera(map: MapLibreMap, moveEnded: () => void): () => void {
  const leader = linked.keys().next().value;
  if (leader !== undefined) {
    copyCamera(leader, map);
  }
  const follow = (): void => {
    if (copying) {
      return;
    }
    copying = true;
    try {
      for (const other of linked.keys()) {
        if (other !== map) {
          copyCamera(map, other);
        }
      }
    } finally {
      copying = false;
    }
  };
  const settle = (): void => {
    if (!copying) linked.forEach((onEnd) => { onEnd(); });
  };
  linked.set(map, moveEnded);
  map.on("move", follow);
  map.on("moveend", settle);
  return () => {
    map.off("move", follow);
    map.off("moveend", settle);
    linked.delete(map);
  };
}

// MapLibre event wiring for the map runtime (DESIGN-frontend F4). The runtime
// passes one handler per event; each handler checks the runtime's disposed flag.

import type { ErrorEvent, Map as MapLibreMap } from "maplibre-gl";

export interface MapEventHandlers {
  /** load: every source has loaded once (the sharppy:map-loaded mark). */
  loaded: () => void;
  /** style.load: the style JSON is applied, so style layers can be changed. */
  styleLoaded: () => void;
  /** sourcedata with isSourceLoaded true. */
  sourceLoaded: (sourceId: string) => void;
  contextLost: () => void;
  dragStart: () => void;
  dragEnd: () => void;
  /** Every MapLibre error; a source error carries its sourceId. */
  error: (event: ErrorEvent, sourceId: string | null) => void;
}

export function attachMapEvents(map: MapLibreMap, handlers: MapEventHandlers): void {
  // Attached first: without an error listener MapLibre prints with console.error.
  map.on("error", (event) => {
    const sourceId: unknown = (event as { sourceId?: unknown }).sourceId;
    handlers.error(event, typeof sourceId === "string" ? sourceId : null);
  });
  map.on("load", handlers.loaded);
  map.on("style.load", handlers.styleLoaded);
  map.on("sourcedata", (event) => {
    if (event.isSourceLoaded) {
      handlers.sourceLoaded(event.sourceId);
    }
  });
  map.on("webglcontextlost", handlers.contextLost);
  map.on("dragstart", handlers.dragStart);
  map.on("dragend", handlers.dragEnd);
}

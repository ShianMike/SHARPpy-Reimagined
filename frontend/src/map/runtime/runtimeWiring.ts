// Store wiring for the map runtime (DESIGN-frontend F3, F4): the start-up
// watch reports into graphicsStore and mapNoticeStore, and the runtime reacts
// to the stores it reads. Nothing here touches MapLibre or deck.gl.

import { useAppStore } from "../../stores/appStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { panelLayers, useMapStore, type FocusRequest } from "../../stores/mapStore";
import { BASEMAP_SOURCE_IDS } from "../basemap/basemapStyle";
import { useMapNoticeStore } from "../notices/mapNoticeStore";
import { documentVisibility, performanceClock } from "./mapOptions";
import { createStartupWatch, type StartupWatch } from "./startupWatch";

/** Inert watch for test targets that do not own a renderer. */
export const NO_WATCH: StartupWatch = {
  sourceLoaded: () => undefined,
  sourceError: () => false,
  afterRender: () => undefined,
  layersSubmitted: () => undefined,
  overlayError: () => false,
  dispose: () => undefined,
};

/** A start-up watch over the active basemap sources, reporting into the stores. */
export function createRuntimeWatch(panel = 0): StartupWatch {
  return createStartupWatch({
    sourceIds: panel === 0 ? BASEMAP_SOURCE_IDS : [],
    visibility: documentVisibility,
    clock: performanceClock,
    sink: {
      basemapIssues: (failed, slow) => {
        if (panel === 0) useMapNoticeStore.getState().setBasemapIssues(failed, slow);
      },
      rendererReady: () => {
        useGraphicsStore.getState().setRendererReady(panel);
        if (panel === 0) useGraphicsStore.getState().setContextLost(false);
      },
      rendererFailed: (kind, message) => {
        console.warn(`[map] panel ${String(panel + 1)} renderer failed (${kind}): ${message}`);
        useGraphicsStore.getState().setRendererFailed(kind, message, panel);
      },
    },
  });
}

export interface StoreReactions {
  /** The selection changed: labels (selected first) and the selection layer. */
  selectionChanged: () => void;
  /** This panel's layer visibility or opacity, or the weather layers' order, changed. */
  layersChanged: (labelsChanged: boolean) => void;
  focus: (request: FocusRequest) => void;
  /** The effective profile changed: pixel ratio, labels. */
  profileChanged: () => void;
  /** The device pixel ratio changed under the same profile. */
  pixelRatioChanged: () => void;
}

/** Subscribes the runtime of one panel to the stores; returns the unsubscribe functions. */
export function subscribeToStores(panel: number, reactions: StoreReactions): (() => void)[] {
  return [
    useAppStore.subscribe((state) => state.selectedStationId, reactions.selectionChanged),
    useAppStore.subscribe((state) => state.selectedPoint, reactions.selectionChanged),
    useMapStore.subscribe((state) => panelLayers(state, panel), (next, previous) => {
      reactions.layersChanged((["stations", "station-labels", "place-labels"] as const).some((id) =>
        next[id].visible !== previous[id].visible,
      ));
    }),
    useMapStore.subscribe((state) => state.layerOrder, () => { reactions.layersChanged(false); }),
    useMapStore.subscribe(
      (state) => state.focusRequest,
      (request) => {
        if (request !== null) {
          reactions.focus(request);
        }
      },
    ),
    useGraphicsStore.subscribe((state) => state.effective.profile, reactions.profileChanged),
    useGraphicsStore.subscribe((state) => state.capabilities?.devicePixelRatio, reactions.pixelRatioChanged),
  ];
}

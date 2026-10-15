// Map notice state (DESIGN-frontend F3): map-local and transient. Written only
// by MapRuntime (from the start-up watch and its error handlers), cleared by
// each new MapRuntime, and read only by MapNotices.

import { create } from "zustand";

export interface MapNoticeState {
  /** Basemap source ids that failed; they stay until the map is reloaded. */
  basemapFailed: readonly string[];
  /** Basemap source ids still loading at the start-up deadline. */
  basemapSlow: readonly string[];
  /** Other MapLibre and deck.gl errors since this map was built. */
  runtimeErrors: number;
  setBasemapIssues: (failed: readonly string[], slow: readonly string[]) => void;
  addRuntimeError: () => void;
  reset: () => void;
}

const EMPTY: readonly string[] = [];

export const useMapNoticeStore = create<MapNoticeState>()((set) => ({
  basemapFailed: EMPTY,
  basemapSlow: EMPTY,
  runtimeErrors: 0,
  setBasemapIssues: (failed, slow) => {
    set({ basemapFailed: [...failed], basemapSlow: [...slow] });
  },
  addRuntimeError: () => {
    set((state) => ({ runtimeErrors: state.runtimeErrors + 1 }));
  },
  reset: () => {
    set({ basemapFailed: EMPTY, basemapSlow: EMPTY, runtimeErrors: 0 });
  },
}));

// Global application state (DESIGN-frontend F3): the active workspace and the
// selected location: a station, or a forecast point picked on the map (never
// both). A station is an id, so it survives a catalogue refetch.

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import { useMapStore } from "./mapStore";
import { useLiveTrackingStore } from "./liveTrackingStore";

export const WORKSPACE_IDS = ["overview", "map", "live", "sounding", "status", "settings"] as const;
export type WorkspaceId = (typeof WORKSPACE_IDS)[number];

export type PendingOutcome = "none" | "selected" | "dropped";

/** A point on the map, in degrees. */
export interface MapPoint {
  latitude: number;
  longitude: number;
}

export interface AppState {
  activeWorkspace: WorkspaceId;
  selectedStationId: string | null;
  /** The forecast point picked on empty map; null while a station is selected. */
  selectedPoint: MapPoint | null;
  /** Restored from preferences and not yet confirmed against a catalogue. */
  pendingStationId: string | null;
  setActiveWorkspace(workspace: WorkspaceId): void;
  selectStation(id: string): void;
  selectPoint(point: MapPoint): void;
  clearSelection(): void;
  setPendingStation(id: string | null): void;
  /**
   * Called on the first successful catalogue load. A pending id in the
   * catalogue becomes the selection; one that is not is dropped with one
   * warning (and preferences then store no selection, so it does not repeat).
   */
  reconcilePendingSelection(hasStation: (id: string) => boolean): PendingOutcome;
}

export const useAppStore = create<AppState>()(
  subscribeWithSelector((set, get) => ({
    activeWorkspace: "overview",
    selectedStationId: null,
    selectedPoint: null,
    pendingStationId: null,
    setActiveWorkspace: (workspace) => {
      if (workspace === "live") useLiveTrackingStore.getState().tick();
      if (workspace === "map" || workspace === "live") useMapStore.setState({ view: workspace });
      set({ activeWorkspace: workspace });
    },
    selectStation: (id) => {
      set({ selectedStationId: id, selectedPoint: null, pendingStationId: null });
    },
    selectPoint: (point) => {
      set({ selectedPoint: { latitude: point.latitude, longitude: point.longitude }, selectedStationId: null, pendingStationId: null });
    },
    clearSelection: () => {
      set({ selectedStationId: null, selectedPoint: null, pendingStationId: null });
    },
    setPendingStation: (id) => {
      set({ pendingStationId: id });
    },
    reconcilePendingSelection: (hasStation) => {
      const pending = get().pendingStationId;
      if (pending === null) {
        return "none";
      }
      if (hasStation(pending)) {
        set({ selectedStationId: pending, pendingStationId: null });
        return "selected";
      }
      console.warn(`[selection] restored station ${pending} is not in the catalogue; the selection was cleared`);
      set({ pendingStationId: null });
      return "dropped";
    },
  })),
);

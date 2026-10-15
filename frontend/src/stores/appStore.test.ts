import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "./appStore";

const initial = useAppStore.getState();
beforeEach(() => {
  useAppStore.setState(initial, true);
});

describe("appStore", () => {
  it("starts on the map with nothing selected", () => {
    expect(useAppStore.getState()).toMatchObject({
      activeWorkspace: "map",
      selectedStationId: null,
      pendingStationId: null,
    });
  });

  it("sets and clears the selection", () => {
    useAppStore.getState().selectStation("72357");
    expect(useAppStore.getState().selectedStationId).toBe("72357");
    useAppStore.getState().selectStation("73111");
    expect(useAppStore.getState().selectedStationId).toBe("73111");
    useAppStore.getState().clearSelection();
    expect(useAppStore.getState().selectedStationId).toBeNull();
  });

  it("selects a station or a forecast point, never both", () => {
    useAppStore.getState().selectStation("72357");
    useAppStore.getState().selectPoint({ latitude: 35.2, longitude: -97.4 });
    expect(useAppStore.getState()).toMatchObject({ selectedStationId: null, selectedPoint: { latitude: 35.2, longitude: -97.4 } });
    useAppStore.getState().selectStation("73111");
    expect(useAppStore.getState()).toMatchObject({ selectedStationId: "73111", selectedPoint: null });
    useAppStore.getState().selectPoint({ latitude: 1, longitude: 2 });
    useAppStore.getState().clearSelection();
    expect(useAppStore.getState()).toMatchObject({ selectedStationId: null, selectedPoint: null });
  });

  it("switches workspaces without touching the selection", () => {
    useAppStore.getState().selectStation("72357");
    useAppStore.getState().setActiveWorkspace("settings");
    expect(useAppStore.getState()).toMatchObject({ activeWorkspace: "settings", selectedStationId: "72357" });
  });

  it("notifies selector subscribers outside React", () => {
    const seen: (string | null)[] = [];
    const unsubscribe = useAppStore.subscribe((state) => state.selectedStationId, (id) => seen.push(id));
    useAppStore.getState().selectStation("72357");
    useAppStore.getState().setActiveWorkspace("sounding");
    useAppStore.getState().clearSelection();
    unsubscribe();
    expect(seen).toEqual(["72357", null]);
  });

  describe("reconcilePendingSelection", () => {
    const inCatalogue = (id: string): boolean => id === "72357";

    it("does nothing without a pending id", () => {
      expect(useAppStore.getState().reconcilePendingSelection(inCatalogue)).toBe("none");
      expect(useAppStore.getState().selectedStationId).toBeNull();
    });

    it("selects a pending id the catalogue contains", () => {
      useAppStore.getState().setPendingStation("72357");
      expect(useAppStore.getState().selectedStationId).toBeNull();
      expect(useAppStore.getState().reconcilePendingSelection(inCatalogue)).toBe("selected");
      expect(useAppStore.getState()).toMatchObject({ selectedStationId: "72357", pendingStationId: null });
    });

    it("drops a pending id the catalogue lacks, with one warning", () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
      useAppStore.getState().setPendingStation("99999");
      expect(useAppStore.getState().reconcilePendingSelection(inCatalogue)).toBe("dropped");
      expect(useAppStore.getState()).toMatchObject({ selectedStationId: null, pendingStationId: null });
      expect(warn).toHaveBeenCalledTimes(1);
      // A second load does not warn again: nothing is pending any more.
      expect(useAppStore.getState().reconcilePendingSelection(inCatalogue)).toBe("none");
      expect(warn).toHaveBeenCalledTimes(1);
    });

    it("keeps the pending id while the catalogue is unavailable", () => {
      useAppStore.getState().setPendingStation("72357");
      // No reconcile call happens until a catalogue loads.
      expect(useAppStore.getState().pendingStationId).toBe("72357");
      expect(useAppStore.getState().selectedStationId).toBeNull();
    });

    it("lets a user selection replace a pending id", () => {
      useAppStore.getState().setPendingStation("72357");
      useAppStore.getState().selectStation("73111");
      expect(useAppStore.getState()).toMatchObject({ selectedStationId: "73111", pendingStationId: null });
      expect(useAppStore.getState().reconcilePendingSelection(inCatalogue)).toBe("none");
    });
  });
});

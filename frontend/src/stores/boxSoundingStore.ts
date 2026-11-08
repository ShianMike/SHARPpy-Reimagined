import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type { Bounds } from "../types/soundings";

interface BoxSoundingState {
  mode: "point" | "box";
  bounds: Bounds | null;
  draft: Bounds | null;
  editing: boolean;
  revision: number;
  setMode: (mode: "point" | "box") => void;
  begin: () => void;
  preview: (bounds: Bounds | null) => void;
  commit: (bounds: Bounds | null) => void;
  cancelEdit: () => void;
  clear: () => void;
}

/** Ephemeral geography; a completed sounding owns its immutable profile. */
export const useBoxSoundingStore = create<BoxSoundingState>()(subscribeWithSelector((set) => ({
  mode: "point", bounds: null, draft: null, editing: false, revision: 0,
  setMode: (mode) => { set((state) => ({ mode, editing: false, draft: null, revision: state.revision + 1 })); },
  begin: () => { set((state) => ({ editing: true, draft: state.bounds, revision: state.revision + 1 })); },
  preview: (draft) => { set({ draft }); },
  commit: (bounds) => { set((state) => ({ bounds, draft: null, editing: false, revision: state.revision + 1 })); },
  cancelEdit: () => { set({ draft: null, editing: false }); },
  clear: () => { set((state) => ({ bounds: null, draft: null, editing: false, revision: state.revision + 1 })); },
})));

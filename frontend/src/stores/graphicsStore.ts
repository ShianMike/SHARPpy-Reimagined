// Graphics state (DESIGN-frontend F3, F6): probe results, the user override,
// the effective profile, and the map renderer's start-up state.

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import { selectGraphicsProfile } from "../lib/graphics/profile";
import { recordLog } from "./logStore";
import type {
  GraphicsCapabilities,
  GraphicsOverride,
  GraphicsSelection,
  RendererError,
  RendererErrorKind,
  RendererState,
} from "../types/graphics";

/** Before the probe runs (main.tsx probes before React renders). */
const NOT_PROBED: GraphicsSelection = { mode: "full", profile: "balanced", reasons: ["Graphics not probed yet"] };

export interface GraphicsState {
  capabilities: GraphicsCapabilities | null;
  override: GraphicsOverride;
  effective: GraphicsSelection;
  renderer: RendererState;
  rendererError: RendererError | null;
  contextLost: boolean;
  /** Remount counter; WeatherMap is keyed by it. */
  mapAttempt: number;
  /** Stores new probe results (startup, or a devicePixelRatio change) and recomputes the profile. */
  setCapabilities(capabilities: GraphicsCapabilities): void;
  setOverride(override: GraphicsOverride): void;
  /** Every new MapRuntime starts here: pending, no error, no lost context. */
  beginRendererAttempt(): void;
  setRendererReady(): void;
  setRendererFailed(kind: RendererErrorKind, message: string): void;
  setContextLost(lost: boolean): void;
  /** Reload map and Try the map again: back to pending, and remount the map. */
  retryMap(): void;
}

function effectiveFor(capabilities: GraphicsCapabilities | null, override: GraphicsOverride): GraphicsSelection {
  return capabilities === null ? NOT_PROBED : selectGraphicsProfile(capabilities, override);
}

export const useGraphicsStore = create<GraphicsState>()(
  subscribeWithSelector((set, get) => ({
    capabilities: null,
    override: "auto",
    effective: NOT_PROBED,
    renderer: "pending",
    rendererError: null,
    contextLost: false,
    mapAttempt: 0,
    setCapabilities: (capabilities) => {
      set({ capabilities, effective: effectiveFor(capabilities, get().override) });
    },
    setOverride: (override) => {
      set({ override, effective: effectiveFor(get().capabilities, override) });
    },
    beginRendererAttempt: () => {
      set({ renderer: "pending", rendererError: null, contextLost: false });
    },
    setRendererReady: () => {
      if (get().renderer !== "ready") {
        set({ renderer: "ready", rendererError: null });
      }
    },
    setRendererFailed: (kind, message) => {
      recordLog("error", "Map renderer", message, kind);
      set({ renderer: "failed", rendererError: { kind, message } });
    },
    setContextLost: (lost) => {
      if (lost !== get().contextLost) recordLog(lost ? "warning" : "info", "Map renderer", lost ? "Graphics context lost" : "Graphics context restored");
      set({ contextLost: lost });
    },
    retryMap: () => {
      set((state) => ({ renderer: "pending", rendererError: null, mapAttempt: state.mapAttempt + 1 }));
    },
  })),
);

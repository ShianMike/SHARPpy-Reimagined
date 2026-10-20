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
  panelErrors: Readonly<Record<number, RendererError>>;
  contextLost: boolean;
  /** Remount counter; WeatherMap is keyed by it. */
  mapAttempt: number;
  /** Stores new probe results (startup, or a devicePixelRatio change) and recomputes the profile. */
  setCapabilities(capabilities: GraphicsCapabilities): void;
  setOverride(override: GraphicsOverride): void;
  /** Every new MapRuntime starts here: pending, no error, no lost context. */
  beginRendererAttempt(panel?: number): void;
  setRendererReady(panel?: number): void;
  setRendererFailed(kind: RendererErrorKind, message: string, panel?: number): void;
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
    panelErrors: {},
    contextLost: false,
    mapAttempt: 0,
    setCapabilities: (capabilities) => {
      set({ capabilities, effective: effectiveFor(capabilities, get().override) });
    },
    setOverride: (override) => {
      set({ override, effective: effectiveFor(get().capabilities, override) });
    },
    beginRendererAttempt: (panel = 0) => {
      const panelErrors = Object.fromEntries(Object.entries(get().panelErrors).filter(([id]) => id !== String(panel)));
      set({ panelErrors, ...(panel === 0 ? { renderer: "pending", rendererError: null, contextLost: false } as const : {}) });
    },
    setRendererReady: (panel = 0) => {
      if (get().panelErrors[panel] !== undefined || panel === 0 && get().renderer !== "ready") {
        const panelErrors = Object.fromEntries(Object.entries(get().panelErrors).filter(([id]) => id !== String(panel)));
        set({ panelErrors, ...(panel === 0 ? { renderer: "ready", rendererError: null } as const : {}) });
      }
    },
    setRendererFailed: (kind, message, panel = 0) => {
      recordLog("error", `Map panel ${String(panel + 1)}`, message, kind);
      const error = { kind, message };
      // Keep an initialized map mounted so its next draw can clear the panel error.
      set({ panelErrors: { ...get().panelErrors, [panel]: error }, ...(panel === 0 && get().renderer !== "ready" ? { renderer: "failed", rendererError: error } as const : {}) });
    },
    setContextLost: (lost) => {
      if (lost !== get().contextLost) recordLog(lost ? "warning" : "info", "Map renderer", lost ? "Graphics context lost" : "Graphics context restored");
      set({ contextLost: lost });
    },
    retryMap: () => {
      set((state) => ({ renderer: "pending", rendererError: null, panelErrors: {}, contextLost: false, mapAttempt: state.mapAttempt + 1 }));
    },
  })),
);

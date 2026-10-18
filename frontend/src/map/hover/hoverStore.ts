// Hover state (DESIGN-frontend F3): map-local and transient. Written only by
// the deck.gl onHover handlers (and cleared when a drag starts); read by the
// tooltips and the field readouts, each through its own slice, so a pointer
// move re-renders only what shows it. Positions are viewport CSS pixels.

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type { SurfacePlot } from "../../types/surface";

export interface HoveredStation {
  stationId: string;
  /** Pointer position in viewport CSS pixels. */
  x: number;
  y: number;
}

/** An SPC outlook or NWS warning/watch area under the pointer. */
export interface HoveredOutlook {
  label: string;
  description: string;
  /** The provider's title and validity. */
  title: string;
  subtitle: string;
  x: number;
  y: number;
}

/** A storm report marker under the pointer. */
export interface HoveredReport {
  observation?: Pick<SurfacePlot, "temperatureC" | "dewpointC">;
  label: string;
  description: string;
  caption: string;
  x: number;
  y: number;
}

/** Where the pointer is on a panel's map, for that panel's field readout. */
export interface PointerPosition {
  panel: number;
  longitude: number;
  latitude: number;
  /** CSS pixels relative to this panel. */
  x: number;
  y: number;
}

export interface HoverState {
  hovered: HoveredStation | null;
  outlook: HoveredOutlook | null;
  report: HoveredReport | null;
  pointer: PointerPosition | null;
  setHovered: (hovered: HoveredStation | null) => void;
  setOutlook: (outlook: HoveredOutlook | null) => void;
  setReport: (report: HoveredReport | null) => void;
  setPointer: (pointer: PointerPosition | null) => void;
  /** A drag started or the runtime went away: nothing is hovered. */
  clear: () => void;
}

const sameStation = (a: HoveredStation | null, b: HoveredStation | null): boolean =>
  a === b || (a !== null && b !== null && a.stationId === b.stationId && a.x === b.x && a.y === b.y);

export const useHoverStore = create<HoverState>()(
  subscribeWithSelector((set, get) => ({
    hovered: null,
    outlook: null,
    report: null,
    pointer: null,
    setHovered: (hovered) => {
      if (!sameStation(get().hovered, hovered)) {
        set({ hovered });
      }
    },
    setOutlook: (outlook) => {
      const current = get().outlook;
      if (current !== outlook && !(current === null && outlook === null)) {
        set({ outlook });
      }
    },
    setReport: (report) => {
      const current = get().report;
      if (current !== report && !(current === null && report === null)) {
        set({ report });
      }
    },
    setPointer: (pointer) => {
      const current = get().pointer;
      if (
        current === pointer ||
        (current !== null &&
          pointer !== null &&
          current.panel === pointer.panel &&
          current.longitude === pointer.longitude &&
          current.latitude === pointer.latitude &&
          current.x === pointer.x &&
          current.y === pointer.y)
      ) {
        return;
      }
      set({ pointer });
    },
    clear: () => {
      set({ hovered: null, outlook: null, report: null, pointer: null });
    },
  })),
);

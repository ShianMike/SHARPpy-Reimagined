// Sounding state (REQUEST §18): the open soundings (backend summaries, newest
// first), the active one, the image's ascent
// choice, and the source forms' inputs. All sounding data stays in the query
// cache; this store holds ids and inputs only.
import { create } from "zustand";
import type { ViewState } from "../features/sounding/viewHistory";
import { subscribeWithSelector } from "zustand/middleware";
import { latestRelease, utcDate } from "../features/sounding/soundingTime";
import type { ObservedProvider, RenderChoice, SoundingSummary } from "../types/soundings";

export type SoundingSource = "observed" | "model" | "era5" | "file";
/** Older soundings drop off the list; the bridge keeps 24, so every listed one stays open. */
export const MAX_OPEN = 12;

export interface ObservedInputs {
  stationId: string;
  /** UTC date, YYYY-MM-DD. */
  date: string;
  hour: number;
  provider: ObservedProvider;
}

export interface ModelInputs {
  modelKey: string | null;
  /** RFC 3339; null takes the model's suggested run. */
  runTime: string | null;
  forecastHour: number | null;
  /** Use the selected station's coordinates rather than the typed ones. */
  atStation: boolean;
  latitude: string;
  longitude: string;
}

export interface Era5Inputs {
  date: string;
  hour: number;
  atStation: boolean;
  latitude: string;
  longitude: string;
}

export interface SoundingState {
  views: Record<string, ViewState>;
  setView: (id: string, view: ViewState) => void;
  open: SoundingSummary[];
  activeId: string | null;
  choice: RenderChoice;
  source: SoundingSource;
  observed: ObservedInputs;
  model: ModelInputs;
  era5: Era5Inputs;
  add: (summary: SoundingSummary) => void;
  setActive: (id: string) => void;
  close: (id: string) => void;
  setChoice: (choice: RenderChoice) => void;
  setSource: (source: SoundingSource) => void;
  setObserved: (patch: Partial<ObservedInputs>) => void;
  setModel: (patch: Partial<ModelInputs>) => void;
  setEra5: (patch: Partial<Era5Inputs>) => void;
}

function initialObserved(): ObservedInputs {
  const { date, hour } = latestRelease(new Date());
  return { stationId: "", date, hour, provider: "auto" };
}

const INITIAL_MODEL: ModelInputs = {
  modelKey: null,
  runTime: null,
  forecastHour: null,
  atStation: true,
  latitude: "",
  longitude: "",
};

export const useSoundingStore = create<SoundingState>()(
  subscribeWithSelector((set) => ({
    open: [],
    views: {},
    setView: (id, view) => { set((state) => ({ views: { ...state.views, [id]: view } })); },
    activeId: null,
    choice: "MU",
    source: "observed",
    observed: initialObserved(),
    model: INITIAL_MODEL,
    // ERA5T arrives about five days late; match the desktop's six-day default.
    era5: { date: utcDate(new Date(Date.now() - 6 * 86_400_000)), hour: 12, atStation: true, latitude: "", longitude: "" },
    add: (summary) => {
      set((state) => {
        const open = [summary, ...state.open.filter((item) => item.soundingId !== summary.soundingId)].slice(0, MAX_OPEN);
        return { open, activeId: summary.soundingId, views: Object.fromEntries(Object.entries(state.views).filter(([id]) => open.some((item) => item.soundingId === id))) };
      });
    },
    setActive: (id) => {
      set((state) => (state.open.some((item) => item.soundingId === id) ? { activeId: id } : {}));
    },
    close: (id) => {
      set((state) => {
        const open = state.open.filter((item) => item.soundingId !== id);
        const activeId = state.activeId === id ? (open[0]?.soundingId ?? null) : state.activeId;
        return { open, activeId, views: Object.fromEntries(Object.entries(state.views).filter(([key]) => key !== id)) };
      });
    },
    setChoice: (choice) => {
      set({ choice });
    },
    setSource: (source) => {
      set({ source });
    },
    setObserved: (patch) => {
      set((state) => ({ observed: { ...state.observed, ...patch } }));
    },
    setModel: (patch) => {
      set((state) => ({ model: { ...state.model, ...patch } }));
    },
    setEra5: (patch) => {
      set((state) => ({ era5: { ...state.era5, ...patch } }));
    },
  })),
);

/** The active sounding's summary, or null. */
export function useActiveSounding(): SoundingSummary | null {
  return useSoundingStore((state) => state.open.find((item) => item.soundingId === state.activeId) ?? null);
}

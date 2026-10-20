// Sounding state (REQUEST §18): the open soundings (backend summaries, newest
// first), the active one, the image's ascent
// choice, and the source forms' inputs. All sounding data stays in the query
// cache; this store holds ids and inputs only.
import { create } from "zustand";
import { hasLocalWork, type ViewState } from "../features/sounding/viewHistory";
import { subscribeWithSelector } from "zustand/middleware";
import { latestRelease, utcDate } from "../features/sounding/soundingTime";
import type { ObservedProvider, RenderChoice, SoundingSummary } from "../types/soundings";

export type SoundingSource = "observed" | "model" | "era5" | "file";
/** Unedited soundings drop off first; discarding local work requires confirmation. */
export const MAX_OPEN = 12;

type SoundingChange = { kind: "open"; summary: SoundingSummary } | { kind: "close"; id: string };

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
  timeIndices: Record<string, number>;
  setTimeIndex: (id: string, index: number) => void;
  setView: (id: string, view: ViewState) => void;
  open: SoundingSummary[];
  activeId: string | null;
  choice: RenderChoice;
  source: SoundingSource;
  observed: ObservedInputs;
  model: ModelInputs;
  era5: Era5Inputs;
  pendingChanges: SoundingChange[];
  resolveDiscard: (discard: boolean) => void;
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

function soundingHasWork(views: Record<string, ViewState>, id: string): boolean {
  return Object.entries(views).some(([key, state]) => (key === id || key.startsWith(`${id}:`)) && hasLocalWork(state));
}

function removalCandidate(open: SoundingSummary[], views: Record<string, ViewState>, change: SoundingChange): SoundingSummary | undefined {
  if (change.kind === "close") return open.find((item) => item.soundingId === change.id);
  const others = open.filter((item) => item.soundingId !== change.summary.soundingId);
  if (others.length < MAX_OPEN) return undefined;
  return others.toReversed().find((item) => !soundingHasWork(views, item.soundingId)) ?? others.at(-1);
}

export function pendingDiscardSounding(state: SoundingState): SoundingSummary | undefined {
  const change = state.pendingChanges[0];
  return change === undefined ? undefined : removalCandidate(state.open, state.views, change);
}

function applyChanges(state: SoundingState, changes: SoundingChange[], discard = false): Partial<SoundingState> {
  let { open, activeId, views } = state;
  // Serialize requests behind the confirmation so concurrent opens cannot replace its target.
  while (changes.length > 0) {
    const change = changes[0];
    if (change === undefined) break;
    const candidate = removalCandidate(open, views, change);
    if (candidate !== undefined && soundingHasWork(views, candidate.soundingId) && !discard) break;
    if (candidate !== undefined) open = open.filter((item) => item.soundingId !== candidate.soundingId);
    if (change.kind === "open") {
      open = [change.summary, ...open.filter((item) => item.soundingId !== change.summary.soundingId)];
      activeId = change.summary.soundingId;
    } else if (activeId === change.id) activeId = open[0]?.soundingId ?? null;
    views = Object.fromEntries(Object.entries(views).filter(([id]) => open.some((item) => item.soundingId === id || id.startsWith(`${item.soundingId}:`))));
    changes = changes.slice(1);
    discard = false;
  }
  return { open, activeId, views, pendingChanges: changes,
    timeIndices: Object.fromEntries(Object.entries(state.timeIndices).filter(([id]) => open.some((item) => item.soundingId === id))) };
}

export const useSoundingStore = create<SoundingState>()(
  subscribeWithSelector((set) => ({
    open: [],
    views: {},
    timeIndices: {},
    setTimeIndex: (id, index) => { set((state) => Number.isInteger(index) && index >= 0 && index < (state.open.find((item) => item.soundingId === id)?.timeCount ?? 0)
      ? { timeIndices: { ...state.timeIndices, [id]: index } } : {}); },
    setView: (id, view) => { set((state) => state.open.some((item) => item.soundingId === id || id.startsWith(`${item.soundingId}:`)) ? { views: { ...state.views, [id]: view } } : {}); },
    activeId: null,
    choice: "MU",
    source: "observed",
    observed: initialObserved(),
    model: INITIAL_MODEL,
    // ERA5T arrives about five days late; match the desktop's six-day default.
    era5: { date: utcDate(new Date(Date.now() - 6 * 86_400_000)), hour: 12, atStation: true, latitude: "", longitude: "" },
    pendingChanges: [],
    resolveDiscard: (discard) => {
      set((state) => applyChanges(state, discard ? state.pendingChanges : state.pendingChanges.slice(1), discard));
    },
    add: (summary) => {
      set((state) => applyChanges(state, [...state.pendingChanges, { kind: "open", summary }]));
    },
    setActive: (id) => {
      set((state) => (state.open.some((item) => item.soundingId === id) ? { activeId: id } : {}));
    },
    close: (id) => {
      set((state) => state.pendingChanges.some((change) => change.kind === "close" && change.id === id)
        ? {} : applyChanges(state, [...state.pendingChanges, { kind: "close", id }]));
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

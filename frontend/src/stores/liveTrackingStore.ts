import { create } from "zustand";

export const LIVE_MINUTE = 60_000;
export const LIVE_HISTORY_MINUTES = 60;
export const liveTimestamp = (time: number): string => new Date(time).toISOString().replace(".000Z", "Z");

/** Stable five-minute addresses keep a loaded hour reusable as the live clock advances. */
export function liveHistoryTimes(now: number): number[] {
  const step = 5 * LIVE_MINUTE;
  const first = Math.ceil((now - LIVE_HISTORY_MINUTES * LIVE_MINUTE) / step) * step;
  const times = Array.from({ length: Math.floor((now - first) / step) + 1 }, (_, index) => first + index * step);
  if (times.at(-1) !== now) times.push(now);
  return times;
}

interface LiveTrackingState {
  now: number;
  selectedTime: number | null;
  playing: boolean;
  tick: (now?: number) => void;
  selectTime: (time: number | null) => void;
  setPlaying: (playing: boolean) => void;
}

/** Data and the live clock are session state; neither belongs in saved preferences. */
export const useLiveTrackingStore = create<LiveTrackingState>((set) => ({
  now: Math.floor(Date.now() / LIVE_MINUTE) * LIVE_MINUTE,
  selectedTime: null,
  playing: false,
  setPlaying: (playing) => { set({ playing }); },
  tick: (now = Date.now()) => {
    if (!Number.isFinite(now)) return;
    const minute = Math.floor(now / LIVE_MINUTE) * LIVE_MINUTE;
    set((state) => ({ now: minute, selectedTime: state.selectedTime === null ? null : Math.min(minute, Math.max(state.selectedTime, minute - LIVE_HISTORY_MINUTES * LIVE_MINUTE)) }));
  },
  selectTime: (time) => {
    if (time !== null && !Number.isFinite(time)) return;
    set((state) => ({ selectedTime: time === null ? null : Math.max(state.now - LIVE_HISTORY_MINUTES * LIVE_MINUTE, Math.min(state.now, Math.floor(time / LIVE_MINUTE) * LIVE_MINUTE)) }));
  },
}));

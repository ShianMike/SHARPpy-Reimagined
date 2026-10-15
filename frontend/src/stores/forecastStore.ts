// The central forecast time (REQUEST §13): one model, run, and forecast hour
// for every map panel, the timeline, the SPC outlook time, and "Open model
// sounding here". Choices only; what exists for them is the backend's answer,
// resolved against the model list in features/forecast/useForecast.ts.

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";

export interface ForecastState {
  /** Null: the map catalogue's field model (HRRR). */
  modelKey: string | null;
  /** RFC 3339; null takes the model's suggested run. */
  runTime: string | null;
  /** Null takes the model's default hour. */
  forecastHour: number | null;
  playing: boolean;
  /** A new model starts at its suggested run and default hour. */
  setModel: (key: string) => void;
  setRun: (runTime: string) => void;
  setHour: (hour: number) => void;
  setPlaying: (playing: boolean) => void;
}

export const useForecastStore = create<ForecastState>()(
  subscribeWithSelector((set) => ({
    modelKey: null,
    runTime: null,
    forecastHour: null,
    playing: false,
    setModel: (key) => {
      set({ modelKey: key, runTime: null, forecastHour: null, playing: false });
    },
    setRun: (runTime) => {
      set({ runTime });
    },
    setHour: (hour) => {
      set({ forecastHour: hour });
    },
    setPlaying: (playing) => {
      set({ playing });
    },
  })),
);

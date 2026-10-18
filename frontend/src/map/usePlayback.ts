// Forecast animation (REQUEST §13, Phase 7): the frontend owns it. While the
// map is mounted this hook prefetches the next hours of every product the
// visible panels draw in parallel within the profile's frame window. Playback
// keeps the current hour visible until the next hour's fields and wind/contour
// data have loaded or failed, and loops at the run's last hour.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useForecastContext } from "../features/forecast/useForecast";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { profileSettings } from "../lib/graphics/profileSettings";
import { useForecastStore } from "../stores/forecastStore";
import { useGraphicsStore } from "../stores/graphicsStore";
import { useUiStore } from "../stores/uiStore";
import { fieldQueryOptions, useFieldFrames } from "./mapQueries";
import { useDrawnProducts } from "./panelField";
import { contourQueryOptions } from "./contourQueries";
import { queryFrame } from "./queryFrame";

/** Minimum time to display each loaded frame, divided by playback speed. */
export const FRAME_INTERVAL_MS = 750;

/** The hour `steps` places after `hour` in `hours`; `wrap` loops past the end. Null past the end. */
export function hourAfter(hours: readonly number[], hour: number, steps: number, wrap: boolean): number | null {
  const index = hours.indexOf(hour);
  if (index === -1 || hours.length === 0) {
    return hours[0] ?? null;
  }
  const target = index + steps;
  if (target >= 0 && target < hours.length) {
    return hours[target] ?? null;
  }
  return wrap ? (hours[((target % hours.length) + hours.length) % hours.length] ?? null) : null;
}

export function usePlayback(): void {
  const context = useForecastContext();
  const products = useDrawnProducts();
  const playing = useForecastStore((state) => state.playing);
  const speed = useUiStore((state) => state.speed);
  const loop = useUiStore((state) => state.loop);
  const frameWindow = useGraphicsStore((state) => profileSettings(state.effective.profile).frameWindow);
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const run = context?.run ?? null;
  const model = context?.model.key ?? "hrrr";
  const hour = context?.hour ?? null;
  const hours = context?.hours ?? null;
  const current = useFieldFrames(
    run === null || hour === null ? [] : products.map((product) => ({ model, product: product.key, runTime: run, forecastHour: hour })),
  );
  const settled = current.every((query) => query.isSuccess || query.isError);
  // While playing, the extra frames of the window are fetched too.
  const ahead = Math.max(1, frameWindow.next + (playing ? frameWindow.prefetched : 0));

  useEffect(() => {
    if (run === null || hour === null || hours === null || !settled) {
      return;
    }
    let stopped = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const upcoming = [...new Set(Array.from({ length: ahead }, (_, i) => hourAfter(hours, hour, i + 1, playing && loop)).filter(
      (next): next is number => next !== null && next !== hour,
    ))];
    const frames = upcoming.map((next) => Promise.all(products.map((product) => {
      const request = { model, product: product.key, runTime: run, forecastHour: next };
      // Failed queries retain their error state for the panel when that hour is shown.
      return Promise.all([
        queryFrame(queryClient, fieldQueryOptions(client, queryClient, request), controller.signal).catch(() => undefined),
        ...(product.contour !== null || product.wind
          ? [queryFrame(queryClient, contourQueryOptions(client, queryClient, request), controller.signal).catch(() => undefined)] : []),
      ]);
    })));
    if (playing) {
      const next = hourAfter(hours, hour, 1, loop);
      const delay = new Promise<void>((resolve) => { timer = setTimeout(resolve, FRAME_INTERVAL_MS / speed); });
      // Loading and the frame's display interval overlap; neither adds a second wait.
      void Promise.all([delay, frames[0]]).then(() => {
        if (stopped) return;
        if (next === null) useForecastStore.getState().setPlaying(false);
        else useForecastStore.getState().setHour(next);
      });
    }
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [model, run, hour, hours, products, settled, ahead, playing, loop, speed, client, queryClient]);
}

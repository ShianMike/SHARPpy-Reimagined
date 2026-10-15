// Forecast animation (REQUEST §13, Phase 7): the frontend owns it. While the
// map is mounted this hook prefetches the next hours of every product the
// visible panels draw, one frame at a time (the profile's frame window says
// how many), and, while playing, steps to the next hour once every panel's
// frame for this hour has loaded or failed. It loops at the run's last hour.

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

/** How long each frame stays up while playing, once it has loaded. */
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
    run === null || hour === null ? [] : products.map((product) => ({ model, product, runTime: run, forecastHour: hour })),
  );
  const settled = current.every((query) => query.isSuccess || query.isError);
  // While playing, the extra frames of the window are fetched too.
  const ahead = Math.max(1, frameWindow.next + (playing ? frameWindow.prefetched : 0));

  useEffect(() => {
    if (run === null || hour === null || hours === null || !settled || products.length === 0) {
      return;
    }
    const stop = new AbortController();
    const upcoming = Array.from({ length: ahead }, (_, i) => hourAfter(hours, hour, i + 1, playing && loop)).filter(
      (next): next is number => next !== null && next !== hour,
    );
    void (async () => {
      for (const next of upcoming) {
        if (stop.signal.aborted) return;
        await Promise.all(products.map((product) =>
          // A failed prefetch is the query's state to show when its hour comes up; nothing to do here.
          queryClient
            .query(fieldQueryOptions(client, queryClient, { model, product, runTime: run, forecastHour: next }))
            .catch(() => undefined),
        ));
      }
    })();
    return () => {
      stop.abort();
    };
  }, [model, run, hour, hours, products, settled, ahead, playing, loop, client, queryClient]);

  useEffect(() => {
    if (!playing || hours === null || hour === null || !settled) {
      return;
    }
    const timer = setTimeout(() => {
      const next = hourAfter(hours, hour, 1, loop);
      if (next !== null) {
        useForecastStore.getState().setHour(next);
      } else {
        useForecastStore.getState().setPlaying(false);
      }
    }, FRAME_INTERVAL_MS / speed);
    return () => {
      clearTimeout(timer);
    };
  }, [playing, hours, hour, settled, speed, loop]);
}

// Forecast animation (REQUEST §13, Phase 7): the frontend owns it. While the
// map is mounted this hook prefetches the next hours of every product the
// visible panels draw in parallel within the profile's frame window. Playback
// keeps the current hour visible while the next hour's fields and enabled
// overlays load, and loops at the run's last hour.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useShallow } from "zustand/react/shallow";
import { shallow } from "zustand/shallow";
import { useAppStore } from "../stores/appStore";
import { useForecastContext } from "../features/forecast/useForecast";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { profileSettings } from "../lib/graphics/profileSettings";
import { useForecastStore } from "../stores/forecastStore";
import { useGraphicsStore } from "../stores/graphicsStore";
import { useUiStore } from "../stores/uiStore";
import { panelLayers, panelProduct, useMapStore, type MapState } from "../stores/mapStore";
import type { FieldProduct } from "../types/map";
import { fieldQueryOptions, useFieldFrames, useMapCatalog } from "./mapQueries";
import { fieldAuxiliaries, productOf, useDrawnProducts } from "./panelField";
import { mapCatalogQueryOptions } from "./catalogQuery";
import { contourQueryOptions } from "./contourQueries";
import { queryFrame } from "./queryFrame";
import { prepareFieldImage } from "./fieldImages";
import { forecastOverlayLoads } from "./forecastOverlayLoads";

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

const playbackLayout = (state: MapState) => ({
  count: state.panelLayout, layers: state.layers, panels: state.panels, product: state.productKey,
  reportWindow: state.reportWindowS, reportFilter: state.reportFilter,
});

export function usePlayback(): void {
  const active = useAppStore((state) => state.activeWorkspace === "map");
  const context = useForecastContext();
  const products = useDrawnProducts();
  const playing = useForecastStore((state) => state.playing);
  const exporting = useForecastStore((state) => state.exporting);
  const speed = useUiStore((state) => state.speed);
  const loop = useUiStore((state) => state.loop);
  const frameWindow = useGraphicsStore((state) => profileSettings(state.effective.profile).frameWindow);
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const run = context?.run ?? null;
  const model = context?.model.key ?? "hrrr";
  const catalogQuery = useMapCatalog();
  const checkingProducts = catalogQuery.isPending || catalogQuery.isPlaceholderData;
  const layout = useMapStore(useShallow(playbackLayout));
  const hour = context?.hour ?? null;
  const hours = context?.hours ?? null;
  const current = useFieldFrames(
    !active || run === null || hour === null ? [] : products.map((product) => ({ model, product: product.key, runTime: run, forecastHour: hour })),
  );
  const settled = current.every((query) => query.isSuccess || query.isError);
  // While playing, the extra frames of the window are fetched too.
  const ahead = Math.max(1, frameWindow.next + (playing ? frameWindow.prefetched : 0));

  useEffect(() => {
    if (!active || exporting || run === null || hour === null || hours === null || !settled || checkingProducts) {
      return;
    }
    let stopped = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const map = useMapStore.getState();
    const panels = Array.from({ length: layout.count }, (_, panel) => panel);
    const next = playing ? hourAfter(hours, hour, 1, loop) : null;
    const forecast = useForecastStore.getState();
    const upcoming = [...new Set(Array.from({ length: ahead }, (_, i) => hourAfter(hours, hour, i + 1, playing && loop)).filter(
      (next): next is number => next !== null && next !== hour,
    ))];
    const frames = upcoming.map(async (forecastHour) => {
      const handoff = forecastHour === next;
      if (!handoff && products.length === 0) return;
      const keepUntil = handoff ? controller.signal : undefined;
      // The target hour can offer a different field or pressure overlay, particularly on a loop.
      const inventory = await queryFrame(queryClient, mapCatalogQueryOptions(client, model, run, forecastHour),
        controller.signal, { keepUntil }).catch(() => undefined);
      if (stopped) return;
      const fields = new Map<FieldProduct, Set<boolean>>();
      if (inventory?.fieldModel === model) {
        for (const panel of panels) {
          const layer = panelLayers(map, panel)["model-field"];
          const product = productOf(inventory, panelProduct(map, panel), panel);
          if (!layer.visible || product === null) continue;
          const modes = fields.get(product) ?? new Set<boolean>();
          modes.add(layer.smoothing === true && product.palette.units !== "flag");
          fields.set(product, modes);
        }
      }
      const frame = { model, runTime: run, forecastHour };
      const tasks: Promise<unknown>[] = [];
      for (const [product, modes] of fields) {
        const request = { ...frame, product: product.key };
        tasks.push(queryFrame(queryClient, fieldQueryOptions(client, queryClient, request), controller.signal, {
          keepUntil,
          prepare: (data) => Promise.all([...modes].sort().map((smooth) =>
            prepareFieldImage(data, product.palette, smooth, controller.signal))),
        }));
        if (fieldAuxiliaries(product)) {
          tasks.push(queryFrame(queryClient, contourQueryOptions(client, queryClient, request), controller.signal, { keepUntil }));
        }
      }
      if (handoff) {
        const pressure = inventory?.fieldModel === model && inventory.products.some((product) => product.key === "mslp");
        for (const panel of panels) {
          tasks.push(...forecastOverlayLoads(client, queryClient, map, panel, frame, pressure, controller.signal, keepUntil));
        }
      }
      // A failed layer must not cut short the wait for the remaining layers.
      await Promise.allSettled(tasks);
    });
    if (playing) {
      const delay = new Promise<void>((resolve) => { timer = setTimeout(resolve, FRAME_INTERVAL_MS / speed); });
      void Promise.all([delay, frames[0]]).then(() => {
        const latest = useForecastStore.getState();
        const settings = useUiStore.getState();
        if (stopped || !latest.playing || latest.exporting || latest.modelKey !== forecast.modelKey
          || latest.runTime !== forecast.runTime || latest.forecastHour !== forecast.forecastHour
          || useAppStore.getState().activeWorkspace !== "map"
          || !shallow(layout, playbackLayout(useMapStore.getState()))
          || settings.loop !== loop || settings.speed !== speed) return;
        if (next === null || next === hour) latest.setPlaying(false);
        else latest.setHour(next);
      });
    }
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, exporting, model, run, hour, hours, products, settled, checkingProducts, ahead, playing, loop, speed, client, queryClient, layout]);
}

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useForecastContext } from "../features/forecast/useForecast";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { useForecastStore } from "../stores/forecastStore";
import { contourQueryOptions } from "./contourQueries";
import { fieldQueryOptions, useMapCatalog } from "./mapQueries";
import { useDrawnFields } from "./panelField";
import { queryFrame } from "./queryFrame";
import { forecastHourMemory, idleForecastHourMemory } from "./forecastHourMemory";

interface DownloadStatus { key: string; done: number; failed: number[]; running: boolean; }
export type ForecastHourState = "cached" | "loading" | "missing" | "failed" | "unknown";
const NO_HOURS: readonly number[] = [];

/** Load the entire selected run through the normal field and disk caches. */
export function useFrameDownload(watchHours = true) {
  const context = useForecastContext();
  const drawnProducts = useDrawnFields();
  const catalogQuery = useMapCatalog();
  const checkingProducts = catalogQuery.isPending || catalogQuery.isPlaceholderData;
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const controller = useRef<AbortController | null>(null);
  const retries = useRef(new Map<number, AbortController>());
  const [status, setStatus] = useState<DownloadStatus | null>(null);
  const model = context?.model.key;
  const run = context?.run;
  const forecastHours = context?.hours ?? NO_HOURS;
  const scope = JSON.stringify([model, run, forecastHours]);
  const nextKey = JSON.stringify([scope, drawnProducts.map(({ product, key }) =>
    `${key}/${String(product.minimumForecastHour)}/${String(product.contour !== null || product.wind)}`).sort()]);
  const [selection, setSelection] = useState({ scope, key: nextKey, products: drawnProducts, hours: forecastHours });
  if (selection.scope !== scope || !checkingProducts && selection.key !== nextKey) {
    setSelection({ scope, key: nextKey, products: drawnProducts, hours: forecastHours });
  }
  // Checking another hour must not replace the active queue's verified selection.
  const key = checkingProducts && selection.scope === scope ? selection.key : nextKey;
  const products = checkingProducts && selection.scope === scope ? selection.products : drawnProducts;
  const minimumForecastHour = Math.max(0, ...products.map(({ product }) => product.minimumForecastHour));
  const hours = context?.hours.filter((hour) => hour >= minimumForecastHour) ?? [];
  const current = status?.key === key ? status : null;
  const total = hours.length;
  const loaded = (current?.done ?? 0) - (current?.failed.length ?? 0);
  const running = current?.running === true;
  const saved = useQuery({ queryKey: ["forecast-cache", key],
    queryFn: () => client.getCachedForecastHours(context?.model.key ?? "hrrr", products.map((entry) => entry.key), context?.run ?? "", hours),
    enabled: context !== null && products.length > 0, retry: false, refetchInterval: running ? 1500 : 30_000 });
  const cached = saved.data ?? [];
  const started = current !== null || cached.length > 0;
  const observe = watchHours || started;
  const memory = useMemo(() => observe && model !== undefined && run !== undefined
    ? forecastHourMemory(queryClient, model, run, selection.hours, selection.products, minimumForecastHour)
    : idleForecastHourMemory, [observe, queryClient, model, run, selection, minimumForecastHour]);
  const memoryStates = useSyncExternalStore(memory.subscribe, memory.snapshot);
  const ready = [...memoryStates.values()].filter((state) => state === "cached").length;
  const hourStates = watchHours ? forecastHours.map((hour): { hour: number; state: ForecastHourState } => {
    const state = memoryStates.get(hour);
    return { hour, state: state === "loading" || state === "failed" || state === "cached" ? state
      : state === "unavailable" || hour < minimumForecastHour ? "missing"
      : current?.failed.includes(hour) === true ? "failed"
      : cached.includes(hour) ? "cached"
      : saved.isSuccess && products.length > 0 ? "missing" : "unknown" };
  }) : [];
  const complete = total > 0 && cached.length === total;
  const missing = hours.filter((hour) => !cached.includes(hour));
  useEffect(() => {
    const jobs = retries.current;
    return () => { controller.current?.abort(); jobs.forEach((job) => { job.abort(); }); jobs.clear(); };
  }, [key]);
  const done = current?.done ?? -1;
  const { refetch } = saved;
  useEffect(() => { if (done >= 0 && !running) void refetch(); }, [running, done, refetch]);

  const loadHour = (hour: number, staleTime: number, signal: AbortSignal) => Promise.allSettled(context === null ? [] : products.flatMap(({ product, key: productKey }) => {
    const request = { model: context.model.key, product: productKey, runTime: context.run, forecastHour: hour };
    const contourRequest = { model: context.model.key, product: product.key, runTime: context.run, forecastHour: hour };
    return [
      queryFrame(queryClient, { ...fieldQueryOptions(client, queryClient, request), staleTime }, signal),
      ...(product.contour !== null || product.wind ? [queryFrame(queryClient, { ...contourQueryOptions(client, queryClient, contourRequest), staleTime }, signal)] : []),
    ];
  }));

  const retryHour = async (hour: number): Promise<void> => {
    if (context === null || products.length === 0 || !hours.includes(hour) || retries.current.has(hour)) return;
    useForecastStore.getState().setPlaying(false);
    const job = new AbortController();
    retries.current.set(hour, job);
    try {
      await queryClient.invalidateQueries({ refetchType: "none", predicate: (query) => {
        const [kind, product, run, frameHour, model] = query.queryKey;
        return (kind === "fields" || kind === "contours") && run === context.run && frameHour === hour && model === context.model.key
          && products.some((item) => item.key === product || item.product.key === product) && query.state.status === "error";
      } });
      const results = await loadHour(hour, Number.POSITIVE_INFINITY, job.signal);
      if (job.signal.aborted) return;
      if (results.every((result) => result.status === "fulfilled")) setStatus((value) => value?.key !== key ? value : {
        ...value, failed: value.failed.filter((failedHour) => failedHour !== hour),
      });
      await refetch();
    } finally { if (retries.current.get(hour) === job) retries.current.delete(hour); }
  };

  const toggle = (): void => {
    if (running) {
      controller.current?.abort();
      setStatus((value) => value === null ? null : { ...value, running: false });
      return;
    }
    if (context === null || products.length === 0) return;
    useForecastStore.getState().setPlaying(false);
    const job = new AbortController();
    controller.current = job;
    setStatus({ key, done: 0, failed: [], running: true });
    let index = 0;
    const worker = async (): Promise<void> => {
      while (!job.signal.aborted) {
        const hour = hours[index++];
        if (hour === undefined) return;
        const staleTime = cached.includes(hour) ? Number.POSITIVE_INFINITY : 0;
        const results = await loadHour(hour, staleTime, job.signal);
        if (controller.current === job && !controller.current.signal.aborted) setStatus((value) => value?.key !== key ? value : {
          ...value, done: value.done + 1, failed: results.some((result) => result.status === "rejected") ? [...value.failed, hour] : value.failed,
        });
      }
    };
    // Bound the queue to two hours; a long run must not flood providers or RAM.
    void Promise.all([worker(), worker()]).then(() => {
      if (controller.current === job) setStatus((value) => value?.key === key ? { ...value, running: false } : value);
    });
  };
  const message = running ? `Downloading forecast hours: ${String(loaded)} of ${String(total)} processed.`
    : complete ? `All ${String(total)} forecast hours saved to cache. ${String(ready)} ready in memory.`
    : (current?.failed.length ?? 0) > 0 ? `${String(current?.failed.length)} forecast hours could not load.`
    : `${String(cached.length)} of ${String(total)} hours saved to cache; ${String(ready)} ready in memory.`;
  return { toggle, running, complete, loaded, total, message,
    started, failed: (current?.failed.length ?? 0) > 0,
    cached: cached.length, ready, missing, failedHours: current?.failed ?? [], cacheError: saved.isError, hourStates, retryHour,
    refresh: () => { void refetch(); },
    disabled: context === null || products.length === 0 || total === 0,
  };
}

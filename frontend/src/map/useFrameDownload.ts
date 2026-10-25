import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useForecastContext } from "../features/forecast/useForecast";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { failureOf } from "../lib/backend/errors";
import { useForecastStore } from "../stores/forecastStore";
import { contourQueryOptions } from "./contourQueries";
import { fieldQueryOptions } from "./mapQueries";
import { useDrawnProducts } from "./panelField";
import { queryFrame } from "./queryFrame";
import { mapKeys } from "../lib/backend/queryOptions";

interface DownloadStatus { key: string; done: number; failed: number[]; running: boolean; }
export type ForecastHourState = "cached" | "loading" | "missing" | "failed" | "unknown";

/** Load the entire selected run through the normal field and disk caches. */
export function useFrameDownload() {
  const context = useForecastContext();
  const products = useDrawnProducts();
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const controller = useRef<AbortController | null>(null);
  const retries = useRef(new Map<number, AbortController>());
  const [status, setStatus] = useState<DownloadStatus | null>(null);
  const key = JSON.stringify([context?.model.key, context?.run, context?.hours, products.map((p) => p.key).sort()]);
  const current = status?.key === key ? status : null;
  const total = context?.hours.length ?? 0;
  const loaded = (current?.done ?? 0) - (current?.failed.length ?? 0);
  const running = current?.running === true;
  const saved = useQuery({ queryKey: ["forecast-cache", key],
    queryFn: () => client.getCachedForecastHours(context?.model.key ?? "hrrr", products.map((product) => product.key), context?.run ?? "", context?.hours ?? []),
    enabled: context !== null && products.length > 0, retry: false, refetchInterval: running ? 1500 : 30_000 });
  const cached = saved.data ?? [];
  const memory = useSyncExternalStore((changed) => queryClient.getQueryCache().subscribe(changed), () =>
    context === null ? "" : context.hours.map((hour) => {
      const states = products.flatMap((product) => {
        const keys = [mapKeys.field(product.key, context.run, hour, context.model.key),
          ...(product.contour !== null || product.wind ? [mapKeys.contours(product.key, context.run, hour, context.model.key)] : [])];
        return keys.map((queryKey) => queryClient.getQueryState(queryKey));
      });
      if (states.some((state) => state?.fetchStatus === "fetching" || state?.fetchStatus === "paused")) return "loading";
      if (states.some((state) => state?.status === "error" && failureOf(state.error)?.code !== "FIELD_UNAVAILABLE")) return "failed";
      if (states.some((state) => state?.status === "error")) return "unavailable";
      return states.length > 0 && states.every((state) => state?.status === "success" && state.data !== undefined) ? "cached" : "missing";
    }).join(","));
  const memoryStates = memory === "" ? [] : memory.split(",");
  const ready = memoryStates.filter((state) => state === "cached").length;
  const hourStates = context?.hours.map((hour, index): { hour: number; state: ForecastHourState } => {
    const state = memoryStates[index];
    return { hour, state: state === "loading" || state === "failed" || state === "cached" ? state
      : state === "unavailable" ? "missing"
      : current?.failed.includes(hour) === true ? "failed"
      : cached.includes(hour) ? "cached"
      : saved.isSuccess && products.length > 0 ? "missing" : "unknown" };
  }) ?? [];
  const complete = total > 0 && cached.length === total;
  const missing = context?.hours.filter((hour) => !cached.includes(hour)) ?? [];
  useEffect(() => {
    const jobs = retries.current;
    return () => { controller.current?.abort(); jobs.forEach((job) => { job.abort(); }); jobs.clear(); };
  }, [key]);
  const done = current?.done ?? -1;
  const { refetch } = saved;
  useEffect(() => { if (done >= 0 && !running) void refetch(); }, [running, done, refetch]);

  const loadHour = (hour: number, staleTime: number, signal: AbortSignal) => Promise.allSettled(context === null ? [] : products.flatMap((product) => {
    const request = { model: context.model.key, product: product.key, runTime: context.run, forecastHour: hour };
    return [
      queryFrame(queryClient, { ...fieldQueryOptions(client, queryClient, request), staleTime }, signal),
      ...(product.contour !== null || product.wind ? [queryFrame(queryClient, { ...contourQueryOptions(client, queryClient, request), staleTime }, signal)] : []),
    ];
  }));

  const retryHour = async (hour: number): Promise<void> => {
    if (context === null || products.length === 0 || !context.hours.includes(hour) || retries.current.has(hour)) return;
    useForecastStore.getState().setPlaying(false);
    const job = new AbortController();
    retries.current.set(hour, job);
    try {
      await queryClient.invalidateQueries({ refetchType: "none", predicate: (query) => {
        const [kind, product, run, frameHour, model] = query.queryKey;
        return (kind === "fields" || kind === "contours") && run === context.run && frameHour === hour && model === context.model.key
          && products.some((item) => item.key === product) && query.state.status === "error";
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
        const hour = context.hours[index++];
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
      if (!job.signal.aborted) setStatus((value) => value?.key === key ? { ...value, running: false } : value);
    });
  };
  const message = running ? `Downloading forecast hours: ${String(loaded)} of ${String(total)} processed.`
    : complete ? `All ${String(total)} forecast hours saved to cache. ${String(ready)} ready in memory.`
    : (current?.failed.length ?? 0) > 0 ? `${String(current?.failed.length)} forecast hours could not load.`
    : `${String(cached.length)} of ${String(total)} hours saved to cache; ${String(ready)} ready in memory.`;
  return { toggle, running, complete, loaded, total, message,
    started: current !== null || cached.length > 0, failed: (current?.failed.length ?? 0) > 0,
    cached: cached.length, ready, missing, failedHours: current?.failed ?? [], cacheError: saved.isError, hourStates, retryHour,
    refresh: () => { void refetch(); },
    disabled: context === null || products.length === 0 || total === 0,
  };
}

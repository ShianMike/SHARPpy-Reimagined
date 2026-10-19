import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useForecastContext } from "../features/forecast/useForecast";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { useForecastStore } from "../stores/forecastStore";
import { contourQueryOptions } from "./contourQueries";
import { fieldQueryOptions } from "./mapQueries";
import { useDrawnProducts } from "./panelField";
import { queryFrame } from "./queryFrame";
import { mapKeys } from "../lib/backend/queryOptions";

interface DownloadStatus { key: string; done: number; failed: number[]; running: boolean; }

/** Load the entire selected run through the normal field and disk caches. */
export function useFrameDownload() {
  const context = useForecastContext();
  const products = useDrawnProducts();
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const controller = useRef<AbortController | null>(null);
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
  const ready = useSyncExternalStore((changed) => queryClient.getQueryCache().subscribe(changed), () =>
    context === null ? 0 : context.hours.filter((hour) => products.length > 0 && products.every((product) => {
      const keys = [mapKeys.field(product.key, context.run, hour, context.model.key),
        ...(product.contour !== null || product.wind ? [mapKeys.contours(product.key, context.run, hour, context.model.key)] : [])];
      return keys.every((queryKey) => queryClient.getQueryState(queryKey)?.status === "success" && queryClient.getQueryData(queryKey) !== undefined);
    })).length);
  const complete = total > 0 && cached.length === total;
  const missing = context?.hours.filter((hour) => !cached.includes(hour)) ?? [];
  useEffect(() => () => { controller.current?.abort(); }, [key]);
  const done = current?.done ?? -1;
  const { refetch } = saved;
  useEffect(() => { if (done >= 0 && !running) void refetch(); }, [running, done, refetch]);

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
        const requests = products.flatMap((product) => {
          const request = { model: context.model.key, product: product.key, runTime: context.run, forecastHour: hour };
          return [
            queryFrame(queryClient, { ...fieldQueryOptions(client, queryClient, request), staleTime }, job.signal),
            ...(product.contour !== null || product.wind ? [queryFrame(queryClient, { ...contourQueryOptions(client, queryClient, request), staleTime }, job.signal)] : []),
          ];
        });
        const results = await Promise.allSettled(requests);
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
  const message = running ? `Downloading forecast hours: ${String(loaded)} of ${String(total)} processed. Click to stop.`
    : complete ? `All ${String(total)} forecast hours saved to cache. ${String(ready)} ready in memory.`
    : (current?.failed.length ?? 0) > 0 ? `${String(current?.failed.length)} forecast hours could not load. Click to retry.`
    : `${String(cached.length)} of ${String(total)} hours saved to cache; ${String(ready)} ready in memory.`;
  return { toggle, running, complete, loaded, total, message,
    started: current !== null || cached.length > 0, failed: (current?.failed.length ?? 0) > 0,
    cached: cached.length, ready, missing, failedHours: current?.failed ?? [], cacheError: saved.isError,
    refresh: () => { void refetch(); },
    disabled: context === null || products.length === 0 || total === 0,
  };
}

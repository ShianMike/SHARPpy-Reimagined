import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useForecastContext } from "../features/forecast/useForecast";
import { useBackendClient } from "../lib/backend/BackendProvider";
import { useForecastStore } from "../stores/forecastStore";
import { contourQueryOptions } from "./contourQueries";
import { fieldQueryOptions } from "./mapQueries";
import { useDrawnProducts } from "./panelField";

interface DownloadStatus { key: string; done: number; failed: number; running: boolean; }

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
  const loaded = (current?.done ?? 0) - (current?.failed ?? 0);
  const running = current?.running === true;
  const complete = total > 0 && loaded === total;
  useEffect(() => () => { controller.current?.abort(); }, [key]);

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
    setStatus({ key, done: 0, failed: 0, running: true });
    let index = 0;
    const worker = async (): Promise<void> => {
      while (!job.signal.aborted) {
        const hour = context.hours[index++];
        if (hour === undefined) return;
        const requests = products.flatMap((product) => {
          const request = { model: context.model.key, product: product.key, runTime: context.run, forecastHour: hour };
          return [
            queryClient.query(fieldQueryOptions(client, queryClient, request)),
            ...(product.contour !== null || product.wind ? [queryClient.query(contourQueryOptions(client, queryClient, request))] : []),
          ];
        });
        const results = await Promise.allSettled(requests);
        if (controller.current === job && !controller.current.signal.aborted) setStatus((value) => value?.key !== key ? value : {
          ...value, done: value.done + 1, failed: value.failed + Number(results.some((result) => result.status === "rejected")),
        });
      }
    };
    // Bound the queue to two hours; a long run must not flood providers or RAM.
    void Promise.all([worker(), worker()]).then(() => {
      if (!job.signal.aborted) setStatus((value) => value?.key === key ? { ...value, running: false } : value);
    });
  };
  const message = running ? `Downloading forecast hours: ${String(loaded)} of ${String(total)} ready. Click to stop.`
    : complete ? `All ${String(total)} forecast hours downloaded for the visible panels.`
    : (current?.failed ?? 0) > 0 ? `${String(current?.failed)} forecast hours could not load. Click to retry.`
    : "Download all forecast hours for the visible panels";
  return { toggle, running, complete, loaded, total, message,
    started: current !== null, failed: (current?.failed ?? 0) > 0,
    disabled: context === null || products.length === 0 || total === 0,
  };
}

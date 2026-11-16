import type { QueryClient } from "@tanstack/react-query";
import type { BackendClient } from "../../lib/backend/client";
import { mapKeys } from "../../lib/backend/queryOptions";
import { fieldQueryOptions } from "../../map/mapQueries";
import { contourQueryOptions } from "../../map/contourQueries";
import { prepareFieldImage } from "../../map/fieldImages";
import { buildLut } from "../../map/field/colourize";
import { queryFrame } from "../../map/queryFrame";
import { mapCaptureSources } from "../../map/runtime/mapCapture";
import { useForecastStore } from "../../stores/forecastStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import type { ForecastContext } from "../forecast/useForecast";
import type { ForecastComposition, CapturedForecast } from "./forecastComposition";
import { composeForecast } from "./forecastComposition";
import { GifWriter } from "./GifWriter";

export interface ForecastExportOptions extends ForecastComposition {
  context: ForecastContext; panel: number; hours: readonly number[]; delay: number; format?: "gif" | "png";
}
export interface ExportProgress { phase: "loading" | "capturing" | "encoding"; done: number; total: number; hour: number }

export function forecastExportFilename(options: ForecastExportOptions): string {
  const run = options.run.replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const label = (hour: number) => `f${String(hour).padStart(3, "0")}`;
  const hours = options.format === "png" ? label(options.hours[0] ?? 0) : `${label(options.hours[0] ?? 0)}-${label(options.hours.at(-1) ?? 0)}`;
  return `sharppy-${options.context.model.key}-${options.product.key}-${run}-${hours}.${options.format ?? "gif"}`
    .replace(/[^a-zA-Z0-9._-]/g, "-");
}

/** Load selected hours through the existing bounded caches; never skip an unavailable forecast. */
export async function exportForecast(options: ForecastExportOptions, client: BackendClient, queryClient: QueryClient,
  signal: AbortSignal, preview: HTMLCanvasElement, progress: (value: ExportProgress) => void): Promise<Blob> {
  signal.throwIfAborted();
  const isGif = options.format !== "png";
  if (isGif && options.hours.length < 2) throw new Error("Select at least two forecast hours.");
  if (!isGif && options.hours.length !== 1) throw new Error("Select one forecast hour for a PNG.");
  const source = mapCaptureSources.get(options.panel);
  if (source === undefined) throw new Error("The selected map is unavailable. Reload maps and try again.");
  const original = useForecastStore.getState();
  if (original.exporting) throw new Error("Another forecast export is running.");
  const originalMap = useMapStore.getState();
  const originalLayer = panelLayers(originalMap, options.panel)["model-field"];
  const setPanelSmoothing = (smoothing: boolean): void => {
    useMapStore.setState((state) => {
      const update = (layers: typeof state.layers) => ({ ...layers, "model-field": { ...layers["model-field"], smoothing } });
      if (originalMap.view === "live") return { livePanels: state.livePanels.map((panel, index) =>
        index === options.panel ? { ...panel, layers: update(panel.layers) } : panel) };
      if (options.panel === 0) return { layers: update(state.layers) };
      return { panels: state.panels.map((panel, index) =>
        index === options.panel - 1 ? { ...panel, layers: update(panel.layers) } : panel) };
    });
  };
  let writer: GifWriter | null = null;
  useForecastStore.setState({ playing: false, exporting: true });
  setPanelSmoothing(true);
  let currentBitmap: ImageBitmap | undefined;
  const snapshots: { png: Blob; frame: CapturedForecast["frame"] }[] = [];
  let snapshotBytes = 0;
  const canvas = document.createElement("canvas");
  const total = options.hours.length;
  // Sample each completed frame, plus the scientific ramp, into one bounded
  // global palette. This avoids first-hour bias and keeps stationary colors stable.
  const sampleCanvas = document.createElement("canvas");
  sampleCanvas.width = 128; sampleCanvas.height = 72;
  const sampleContext = isGif ? sampleCanvas.getContext("2d", { willReadFrequently: true }) : null;
  const samplesPerFrame = Math.max(1, Math.min(4096, Math.floor(131_072 / total)));
  const rampSamples = Math.min(32_768, Math.max(1024, Math.floor(samplesPerFrame * total / 2)));
  const paletteSample = isGif ? new Uint8Array((samplesPerFrame * total + rampSamples) * 4) : undefined;
  let done = 0;
  const write = async (captured: CapturedForecast): Promise<void> => {
    signal.throwIfAborted();
    composeForecast(canvas, captured, options);
    const pixels = canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height);
    if (pixels === undefined) throw new Error("The map image could not be read.");
    if (writer === null) throw new Error("GIF encoding is unavailable.");
    await writer.write({ rgba: pixels.data.buffer, width: canvas.width, height: canvas.height,
      delay: options.delay, ...(done === 0 && paletteSample !== undefined ? { paletteSample } : {}) });
    preview.width = canvas.width; preview.height = canvas.height;
    preview.getContext("2d")?.drawImage(canvas, 0, 0);
    progress({ phase: "encoding", done: ++done, total, hour: captured.frame.forecastHour });
  };
  const load = async (hour: number, loadSignal = signal) => {
    const request = { model: options.context.model.key, product: options.product.key, runTime: options.run, forecastHour: hour };
    const catalog = await queryFrame(queryClient, { queryKey: mapKeys.catalog(request.model, request.runTime, hour),
      queryFn: () => client.getMapCatalog(request.model, request.runTime, hour), staleTime: 10 * 60_000 }, loadSignal);
    if (!catalog.products.some((product) => product.key === request.product)) throw new Error(`F${String(hour).padStart(3, "0")} does not offer ${options.product.label}. Choose different hours.`);
    const [frame] = await Promise.all([
      queryFrame(queryClient, fieldQueryOptions(client, queryClient, request), loadSignal),
      ...(options.product.contour !== null || options.product.wind
        ? [queryFrame(queryClient, contourQueryOptions(client, queryClient, request), loadSignal)] : []),
    ]);
    return { request, frame };
  };
  try {
    if (isGif && sampleContext === null) throw new Error("GIF color sampling is unavailable.");
    let next = 0, loaded = 0;
    const failedPreload = new AbortController();
    const preloadSignal = AbortSignal.any([signal, failedPreload.signal]);
    progress({ phase: "loading", done: 0, total: options.hours.length, hour: options.hours[0] ?? 0 });
    const preload = async (): Promise<void> => {
      try {
        while (!preloadSignal.aborted) {
          const hour = options.hours[next++];
          if (hour === undefined) return;
          await load(hour, preloadSignal);
          preloadSignal.throwIfAborted();
          progress({ phase: "loading", done: ++loaded, total: options.hours.length, hour });
        }
      } catch (failure) { failedPreload.abort(failure); throw failure; }
    };
    // Verify every selected hour before any capture. Bound provider and decode concurrency to two hours.
    await Promise.allSettled([preload(), preload()]);
    failedPreload.signal.throwIfAborted();
    signal.throwIfAborted();
    for (const [index, hour] of options.hours.entries()) {
      signal.throwIfAborted();
      if (mapCaptureSources.get(options.panel) !== source) throw new Error("The map changed during export. Try exporting again.");
      progress({ phase: "capturing", done: index, total: options.hours.length, hour });
      // The regular memory/disk caches own data retention; avoid pinning an entire run's raw grids in RAM.
      const { request, frame } = await load(hour);
      await prepareFieldImage(frame, options.product.palette, true, signal);
      signal.throwIfAborted();
      useForecastStore.getState().setHour(hour);
      const map = await source.captureFrame(request, signal, { width: options.width, height: options.height });
      const metadata = { forecastHour: frame.forecastHour, validTime: frame.validTime, attribution: frame.attribution,
        basemapAttribution: source.attribution?.() ?? "Natural Earth" };
      let png: Blob;
      try {
        composeForecast(preview, { map, frame: metadata }, options);
        if (sampleContext !== null && paletteSample !== undefined) {
          sampleContext.imageSmoothingEnabled = false;
          sampleContext.drawImage(preview, 0, 0, sampleCanvas.width, sampleCanvas.height);
          const pixels = sampleContext.getImageData(0, 0, sampleCanvas.width, sampleCanvas.height).data;
          for (let i = 0; i < samplesPerFrame; i++) {
            const from = Math.floor(i * (pixels.length / 4) / samplesPerFrame) * 4;
            paletteSample.set(pixels.subarray(from, from + 4), (index * samplesPerFrame + i) * 4);
          }
        }
        png = await new Promise<Blob>((resolve, reject) => {
          (isGif ? map : preview).toBlob((blob) => { if (blob === null) reject(new Error("The map image could not be saved.")); else resolve(blob); }, "image/png");
        });
      } finally { map.width = 0; map.height = 0; }
      signal.throwIfAborted();
      if (!isGif) {
        progress({ phase: "capturing", done: 1, total: 1, hour });
        return png;
      }
      snapshotBytes += png.size;
      if (snapshotBytes > 128 * 1024 * 1024) throw new Error("The lossless frames exceed 128 MiB. Choose fewer hours or a smaller size.");
      snapshots.push({ png, frame: metadata });
      progress({ phase: "capturing", done: index + 1, total: options.hours.length, hour });
    }
    // All real-hour frames now exist as lossless PNGs. Encode only after that set is complete.
    if (paletteSample !== undefined) {
      const colors = buildLut(options.product.palette);
      for (let i = 0; i < rampSamples; i++) {
        const from = Math.floor(i * (colors.length / 4) / rampSamples) * 4;
        paletteSample.set(colors.subarray(from, from + 4), (samplesPerFrame * total + i) * 4);
      }
    }
    writer = new GifWriter(signal);
    progress({ phase: "encoding", done: 0, total, hour: options.hours[0] ?? 0 });
    for (let snapshot = snapshots.shift(); snapshot !== undefined; snapshot = snapshots.shift()) {
      signal.throwIfAborted();
      currentBitmap = await createImageBitmap(snapshot.png);
      const captured = { map: currentBitmap, frame: snapshot.frame };
      await write(captured);
      currentBitmap.close();
      currentBitmap = undefined;
    }
    signal.throwIfAborted();
    return writer.finish();
  } finally {
    writer?.close();
    currentBitmap?.close();
    setPanelSmoothing(originalLayer.smoothing ?? false);
    const current = useForecastStore.getState();
    useForecastStore.setState({ exporting: false, ...(current.modelKey === original.modelKey && current.runTime === original.runTime
      ? { forecastHour: original.forecastHour, playing: false } : {}) });
  }
}

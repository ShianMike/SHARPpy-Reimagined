import type { QueryClient } from "@tanstack/react-query";
import type { BackendClient } from "../../lib/backend/client";
import { fieldQueryOptions, type FrameRequest } from "../../map/mapQueries";
import { mapCatalogQueryOptions } from "../../map/catalogQuery";
import { contourQueryOptions } from "../../map/contourQueries";
import { forecastOverlayLoads } from "../../map/forecastOverlayLoads";
import { fieldAuxiliaries, productOf } from "../../map/panelField";
import { prepareFieldImage } from "../../map/fieldImages";
import { buildLut } from "../../map/field/colourize";
import { trackForecastDrawingCapture } from "../../map/forecastDrawing/forecastDrawingCapture";
import { layoutForecastDrawingFooter } from "../../map/forecastDrawing/forecastDrawingExportFooter";
import { queryFrame } from "../../map/queryFrame";
import { mapCaptureSources } from "../../map/runtime/mapCapture";
import { useForecastStore } from "../../stores/forecastStore";
import { panelLayers, panelProduct, panelReports, useMapStore } from "../../stores/mapStore";
import type { ForecastContext } from "../forecast/useForecast";
import type { FieldProduct } from "../../types/map";
import { encodeAnimation, type AnimationFrame } from "../animation/encodeAnimation";
import { composeForecast, type ForecastComposition } from "./forecastComposition";
export interface ForecastExportOptions extends ForecastComposition {
    context: ForecastContext;
    panel: number;
    productKey?: string;
    hours: readonly number[];
    delay: number;
    format?: "gif" | "png" | "webp";
    panels?: readonly {
        panel: number;
        product: FieldProduct;
        productKey?: string;
    }[];
}
export interface ExportProgress {
    phase: "loading" | "capturing" | "encoding";
    done: number;
    total: number;
    hour: number;
}
export function forecastExportFilename(options: ForecastExportOptions): string {
    const run = options.run.replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
    const label = (hour: number) => `f${String(hour).padStart(3, "0")}`;
    const hours = options.format === "png" ? label(options.hours[0] ?? 0) : `${label(options.hours[0] ?? 0)}-${label(options.hours.at(-1) ?? 0)}`;
    const count = options.panels?.length ?? 1;
    const product = count > 1 ? `layout-${String(count)}` : (options.productKey ?? options.product.key);
    return `sharppy-${options.context.model.key}-${product}-${run}-${hours}.${options.format ?? "gif"}`.replace(/[^a-zA-Z0-9._-]/g, "-");
}
/** All selected panels and hours exist as lossless PNGs before encoding begins. */
export async function exportForecast(options: ForecastExportOptions, client: BackendClient, queryClient: QueryClient, callerSignal: AbortSignal, preview: HTMLCanvasElement, progress: (value: ExportProgress) => void): Promise<Blob> {
    callerSignal.throwIfAborted();
    const format = options.format ?? "gif", animated = format !== "png";
    if (animated && options.hours.length < 2)
        throw new Error("Select at least two forecast hours.");
    if (!animated && options.hours.length !== 1)
        throw new Error("Select one forecast hour for a PNG.");
    const panels = options.panels ?? [{ panel: options.panel, product: options.product, ...(options.productKey === undefined ? {} : { productKey: options.productKey }) }];
    if (![1, 2, 4].includes(panels.length))
        throw new Error("Choose one panel or a complete two- or four-panel layout.");
    const sources = panels.map(part => {
        const source = mapCaptureSources.get(part.panel);
        if (source === undefined)
            throw new Error("A selected map is unavailable. Reload maps and try again.");
        return source;
    });
    const original = useForecastStore.getState(), originalMap = useMapStore.getState();
    if (original.exporting)
        throw new Error("Another forecast export is running.");
    const originalLayers = panels.map(part => panelLayers(originalMap, part.panel)["model-field"]);
    const smooth = (restore: boolean): void => {
        for (const [i, part] of panels.entries()) {
            const smoothing = restore ? originalLayers[i]?.smoothing ?? false : true;
            useMapStore.setState(state => {
                const update = (layers: typeof state.layers) => ({ ...layers, "model-field": { ...layers["model-field"], smoothing } });
                if (originalMap.view === "live")
                    return { livePanels: state.livePanels.map((p, index) => index === part.panel ? { ...p, layers: update(p.layers) } : p) };
                return part.panel === 0 ? { layers: update(state.layers) } : { panels: state.panels.map((p, index) => index === part.panel - 1 ? { ...p, layers: update(p.layers) } : p) };
            });
        }
    };
    const load = async (part: typeof panels[number], hour: number, loadSignal: AbortSignal, keepUntil?: AbortSignal) => {
        const requestKey = part.productKey ?? part.product.key;
        const request: FrameRequest = { model: options.context.model.key, product: requestKey, runTime: options.run, forecastHour: hour };
        const catalog = await queryFrame(queryClient, mapCatalogQueryOptions(client, request.model, request.runTime, hour), loadSignal, { keepUntil });
        const product = productOf(catalog, requestKey, part.panel);
        if (catalog.fieldModel !== request.model || product === null || hour < product.minimumForecastHour)
            throw new Error(`Panel ${String(part.panel + 1)} does not offer ${part.product.label} at F${String(hour).padStart(3, "0")}.`);
        const pressureAvailable = catalog.products.some(product => product.key === "mslp");
        if (panelLayers(originalMap, part.panel).isobars.visible && !pressureAvailable)
            throw new Error(`Panel ${String(part.panel + 1)} cannot load its enabled isobars at F${String(hour).padStart(3, "0")}.`);
        const [frame] = await Promise.all([
            queryFrame(queryClient, fieldQueryOptions(client, queryClient, request), loadSignal, { keepUntil }),
            ...(fieldAuxiliaries(product) ? [queryFrame(queryClient, contourQueryOptions(client, queryClient, { ...request, product: product.key }), loadSignal, { keepUntil })] : []),
            ...forecastOverlayLoads(client, queryClient, originalMap, part.panel, request, pressureAvailable, loadSignal, keepUntil),
        ]);
        return { request, frame };
    };
    const frames: AnimationFrame[] = [];
    const canvas = document.createElement("canvas");
    canvas.width = options.width;
    canvas.height = options.height;
    const context = canvas.getContext("2d");
    if (context === null)
        throw new Error("Map export is unavailable.");
    const drawing = trackForecastDrawingCapture(callerSignal);
    const { signal } = drawing;
    let bytes = 0;
    let displayedHour = original.forecastHour;
    try {
        useForecastStore.setState({ playing: false, exporting: true });
        smooth(false);
        const captureState = useMapStore.getState();
        const checkSelection = (): void => {
            signal.throwIfAborted();
            const forecast = useForecastStore.getState(), mapState = useMapStore.getState();
            if (forecast.modelKey !== original.modelKey || forecast.runTime !== original.runTime || forecast.forecastHour !== displayedHour)
                throw new Error("The selected forecast changed during export. Try again.");
            if (mapState.panelLayout !== captureState.panelLayout || mapState.view !== captureState.view)
                throw new Error("The map layout changed during export. Try again.");
            if (mapState.layerOrder !== captureState.layerOrder || panels.some(({ panel }) =>
                panelLayers(mapState, panel) !== panelLayers(captureState, panel)
                || panelProduct(mapState, panel) !== panelProduct(captureState, panel)
                || panelReports(mapState, panel).reportFilter !== panelReports(captureState, panel).reportFilter
                || panelReports(mapState, panel).reportWindowS !== panelReports(captureState, panel).reportWindowS))
                throw new Error("The map products, layers or storm-report settings changed during export. Try again.");
        };
        const footer = drawing.kind === "unofficial"
            ? layoutForecastDrawingFooter(context, options.width, { details: drawing.details, product: drawing.product }) : null;
        const columns = panels.length === 1 ? 1 : 2, rows = panels.length === 4 ? 2 : 1;
        const cellWidth = options.width / columns;
        const cellHeight = footer === null ? options.height / rows : Math.floor((options.height - footer.height) / rows);
        if (cellHeight <= 0) throw new Error("The export is too small for the forecast drawing disclaimer. Choose a larger size.");
        const pending = options.hours.flatMap(hour => panels.map(part => ({ part, hour })));
        let next = 0, loaded = 0;
        const failed = new AbortController(), preloadSignal = AbortSignal.any([signal, failed.signal]);
        progress({ phase: "loading", done: 0, total: pending.length, hour: options.hours[0] ?? 0 });
        const preload = async (): Promise<void> => {
            try {
                while (!preloadSignal.aborted) {
                    const item = pending[next++];
                    if (item === undefined)
                        return;
                    await load(item.part, item.hour, preloadSignal);
                    preloadSignal.throwIfAborted();
                    progress({ phase: "loading", done: ++loaded, total: pending.length, hour: item.hour });
                }
            }
            catch (error) {
                failed.abort(error);
                throw error;
            }
        };
        await Promise.allSettled([preload(), preload()]);
        failed.signal.throwIfAborted();
        signal.throwIfAborted();
        for (const [index, hour] of options.hours.entries()) {
            checkSelection();
            const hold = new AbortController();
            const loadSignal = AbortSignal.any([signal, hold.signal]);
            try {
                const loaded: Awaited<ReturnType<typeof load>>[] = [];
                for (const part of panels) {
                    const item = await load(part, hour, loadSignal, loadSignal);
                    await prepareFieldImage(item.frame, part.product.palette, true, loadSignal);
                    loaded.push(item);
                }
                checkSelection();
                if (loaded.some(item => item.frame.validTime !== loaded[0]?.frame.validTime))
                    throw new Error("The selected panels do not have the same valid time.");
                displayedHour = hour;
                useForecastStore.getState().setHour(hour);
                progress({ phase: "capturing", done: index, total: options.hours.length, hour });
                for (const [i, part] of panels.entries()) {
                    if (mapCaptureSources.get(part.panel) !== sources[i])
                        throw new Error("A map changed during export. Try again.");
                    const item = loaded[i], source = sources[i];
                    if (item === undefined || source === undefined)
                        throw new Error("A selected panel is unavailable.");
                    const resolution = { width: cellWidth, height: cellHeight };
                    const map = drawing.kind === "unofficial" ? await source.captureFrame(item.request, loadSignal, resolution, drawing)
                        : await source.captureFrame(item.request, loadSignal, resolution);
                    const cell = document.createElement("canvas");
                    try {
                        checkSelection();
                        composeForecast(cell, { map, frame: { forecastHour: hour, validTime: item.frame.validTime, attribution: item.frame.attribution,
                                ...(source.attribution === undefined ? {} : { basemapAttribution: source.attribution() }) } }, { ...options, width: cellWidth, height: cellHeight, product: part.product });
                        context.drawImage(cell, (i % columns) * cellWidth, Math.floor(i / columns) * cellHeight, cellWidth, cellHeight);
                    }
                    finally {
                        map.width = 0;
                        map.height = 0;
                        cell.width = 0;
                        cell.height = 0;
                    }
                }
            }
            finally { hold.abort(); }
            footer?.draw(cellHeight * rows, options.height);
            const png = await new Promise<Blob>((resolve, reject) => {
                canvas.toBlob(blob => {
                    if (blob === null)
                        reject(new Error("The map image could not be saved."));
                    else
                        resolve(blob);
                }, "image/png");
            });
            checkSelection();
            preview.width = options.width;
            preview.height = options.height;
            preview.getContext("2d")?.drawImage(canvas, 0, 0);
            if (!animated) {
                progress({ phase: "capturing", done: 1, total: 1, hour });
                signal.throwIfAborted();
                return png;
            }
            bytes += png.size;
            if (bytes > 128 * 1024 * 1024)
                throw new Error("The lossless frames exceed 128 MiB. Choose fewer hours or a smaller size.");
            frames.push({ image: png, label: String(hour) });
            progress({ phase: "capturing", done: index + 1, total: options.hours.length, hour });
        }
        const ramps = panels.map(part => buildLut(part.product.palette));
        const ramp = new Uint8Array(ramps.reduce((n, item) => n + item.length, 0));
        let offset = 0;
        for (const item of ramps) {
            ramp.set(item, offset);
            offset += item.length;
        }
        progress({ phase: "encoding", done: 0, total: frames.length, hour: options.hours[0] ?? 0 });
        const result = await encodeAnimation(frames, options.width, options.height, format as "gif" | "webp", options.delay, client, signal, done => { progress({ phase: "encoding", done, total: frames.length, hour: options.hours[done - 1] ?? 0 }); }, preview, ramp);
        checkSelection();
        return result;
    }
    finally {
        drawing.dispose();
        frames.length = 0;
        canvas.width = 0;
        canvas.height = 0;
        smooth(true);
        const current = useForecastStore.getState();
        useForecastStore.setState({ exporting: false, ...(current.modelKey === original.modelKey && current.runTime === original.runTime && current.forecastHour === displayedHour ?
                { forecastHour: original.forecastHour, playing: false } : {}) });
    }
}

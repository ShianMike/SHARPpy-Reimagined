import { Copy, Download, Film, GripHorizontal, Image, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Popover } from "../../components/Popover";
import { Notice } from "../../components/Notice";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { copyPngToClipboard } from "../../lib/imageClipboard";
import { useMapStore, panelLayers, panelProduct } from "../../stores/mapStore";
import { useMapCatalog } from "../../map/catalogQuery";
import { productOf } from "../../map/panelField";
import { useForecastStore } from "../../stores/forecastStore";
import { useUiStore } from "../../stores/uiStore";
import { Action, IconAction } from "../../ui/Controls";
import { SourceSelect } from "../sounding/SourceFields";
import { compactUtc, forecastLabel } from "../sounding/soundingTime";
import type { ForecastContext } from "../forecast/useForecast";
import type { FieldProduct } from "../../types/map";
import { exportForecast, forecastExportFilename, type ExportProgress, type ForecastExportOptions } from "./exportForecast";
import { copyForecastGif } from "./forecastClipboard";
import { useObjectUrl } from "../sounding/soundingQueries";
import styles from "./ForecastExportDialog.module.css";

const SIZES = [{ value: 1280, label: "1280 × 720 · HD" }, { value: 1920, label: "1920 × 1080 · Full HD" },
  { value: 2560, label: "2560 × 1440 · QHD" }, { value: 3840, label: "3840 × 2160 · UHD" }];
const STAGES = { loading: "Loading forecast", capturing: "Capturing maps", encoding: "Encoding animation" };

export function ForecastExportDialog({ anchor, context, product, panel, onClose }: {
  anchor: HTMLButtonElement; context: ForecastContext; product: FieldProduct; panel: number; onClose: () => void;
}): ReactNode {
  const map = useMapStore();
  const catalog = useMapCatalog().data;
  const [scope, setScope] = useState("panel");
  const parts = scope === "layout" ? Array.from({ length: map.panelLayout }, (_, index) => ({
    panel: index, product: productOf(catalog, panelProduct(map, index), index) ?? undefined,
  })) : [{ panel, product }];
  const available = parts.every((part) => part.product !== undefined && panelLayers(map, part.panel)["model-field"].visible);
  const hours = context.hours.filter((hour) => parts.every((part) => part.product !== undefined && hour >= part.product.minimumForecastHour));
  const [selected, setSelected] = useState(() => hours.slice(Math.max(0, hours.indexOf(context.hour)), Math.max(0, hours.indexOf(context.hour)) + 12));
  const [width, setWidth] = useState(1920);
  const [delay, setDelay] = useState(750);
  const [progress, setProgress] = useState<ExportProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ blob: Blob; filename: string; format: "gif" | "png" | "webp" } | null>(null);
  const [activeFormat, setActiveFormat] = useState<"gif" | "png" | "webp">("gif");
  const [copy, setCopy] = useState<{ blob: Blob; state: "pending" | "copied" | "failed"; message?: string } | null>(null);
  const url = useObjectUrl(result?.blob);
  const controller = useRef<AbortController | null>(null);
  const preview = useRef<HTMLCanvasElement>(null);
  const client = useBackendClient();
  const queryClient = useQueryClient();
  const units = useUiStore((state) => state.units);
  const selectedHours = hours.filter((hour) => selected.includes(hour));
  const height = width * 9 / 16;
  const resetResult = (): void => { setResult(null); setProgress(null); setError(""); setCopy(null); };
  useEffect(() => {
    useForecastStore.getState().setPlaying(false);
    return () => { controller.current?.abort(); };
  }, []);
  const close = (): void => { controller.current?.abort(); onClose(); anchor.focus(); };
  const start = (format: "gif" | "png" | "webp"): void => {
    if (busy || preview.current === null) return;
    const job = new AbortController();
    controller.current = job;
    setBusy(true); setError(""); setResult(null); setProgress(null); setCopy(null); setActiveFormat(format);
    const options: ForecastExportOptions = { context, product, panel, model: context.model.label, run: context.run,
      hours: format === "png" ? [context.hour] : selectedHours, width, height, delay, units, format,
      panels: parts.flatMap((part) => part.product === undefined ? [] : [{ panel: part.panel, product: part.product }]) };
    // Fixture builds may observe the existing callbacks without adding work to production exports.
    const benchmark = import.meta.env.MODE === "fixture" && format === "gif" ? window.__SHARPPY_GIF_BENCHMARK__ : undefined;
    benchmark?.start(options);
    void exportForecast(options, client, queryClient, job.signal, preview.current, (value) => {
      benchmark?.progress(value);
      if (!job.signal.aborted) setProgress(value);
    }).then((blob) => {
      benchmark?.finish(blob);
      if (!job.signal.aborted) setResult({ blob, filename: forecastExportFilename(options), format });
    }, (failure: unknown) => {
      benchmark?.fail(failure);
      if (!job.signal.aborted) setError(failure instanceof Error ? failure.message : "Export failed. Check the selected hours and try again.");
    }).finally(() => {
      if (controller.current === job) { controller.current = null; setBusy(false); }
    });
  };
  const currentCopy = copy?.blob === result?.blob ? copy : null;
  const copyGif = async (): Promise<void> => {
    if (result?.format !== "gif" || currentCopy?.state === "pending") return;
    const blob = result.blob;
    setCopy({ blob, state: "pending" });
    try {
      await copyForecastGif(blob);
      setCopy((current) => current?.blob === blob ? { blob, state: "copied" } : current);
    } catch (failure) { setCopy((current) => current?.blob === blob ? { blob, state: "failed", message: failure instanceof Error && failure.message.startsWith("GIFs over")
      ? failure.message : "This device or browser could not copy the GIF. Save the GIF instead." } : current); }
  };
  const copyPng = async (): Promise<void> => {
    if (result?.format !== "png" || currentCopy?.state === "pending") return;
    const blob = result.blob;
    setCopy({ blob, state: "pending" });
    try {
      await copyPngToClipboard(blob);
      setCopy((current) => current?.blob === blob ? { blob, state: "copied" } : current);
    } catch {
      setCopy((current) => current?.blob === blob ? { blob, state: "failed", message: "This device or browser could not copy the PNG. Save the PNG instead." } : current);
    }
  };
  const copyFormat = result?.format === "png" ? "PNG" : "GIF";
  const status = busy && progress !== null ? `${STAGES[progress.phase]} · ${String(progress.done)} / ${String(progress.total)} · ${forecastLabel(progress.hour)}`
    : busy ? "Preparing export…" : currentCopy?.state === "pending" ? `Copying ${copyFormat}…` : currentCopy?.state === "copied" ? `${copyFormat} copied`
    : result !== null ? `${result.format.toUpperCase()} ready · ${(result.blob.size / 1024 ** 2).toFixed(2)} MiB`
    : progress !== null && error === "" ? "Export cancelled. You can try again."
    : selectedHours.length < 2 ? "Select at least two hours to animate, or export the current hour as PNG." : "Ready to export";
  return <Popover id="forecast-gif-export" open anchor={anchor} onClose={close} role="dialog" label="Export forecast" initialFocus="container" movable backdrop className={styles.dialog}>
    <header className={styles.header} data-popover-drag>
      <IconAction icon={GripHorizontal} size="sm" label="Move export screen" data-popover-move />
      <div><h2>Export forecast</h2><p>{context.model.label} · Run {compactUtc(context.run)} UTC</p></div>
      <IconAction icon={X} size="sm" label="Close export screen" onClick={close} />
    </header>
    <div className={styles.body}>
      <div className={styles.settings}>
        <section className={styles.identity} aria-label="Export parameter"><span>{scope === "layout" ? "Complete map layout" : `Selected parameter · Panel ${String(panel + 1)}`}</span><strong>{parts.map((part) => part.product?.label ?? "Unavailable parameter").join(" · ")}</strong><p>{scope === "layout" ? "Each panel keeps its parameter, color scale and valid time." : product.caveat || "Exports the current map extent with its color scale and valid times."}</p></section>
        {map.panelLayout > 1 ? <SourceSelect label="Export area" value={scope} disabled={busy} options={[{ value: "panel", label: `Panel ${String(panel + 1)} only` }, { value: "layout", label: `Whole layout · ${String(map.panelLayout)} panels` }]} onChange={(value) => { resetResult(); setScope(value); }} /> : null}
        {available ? null : <Notice tone="danger" title="A map panel is unavailable" message="Show a forecast parameter in every selected panel before exporting." />}
        <fieldset disabled={busy} className={styles.hoursSection}>
          <legend>Forecast hours <span>{selectedHours.length} selected</span></legend>
          <div className={styles.hourActions}><Action onClick={() => { resetResult(); setSelected(hours); }}>Select all</Action><Action onClick={() => { resetResult(); setSelected([]); }}>Clear</Action></div>
          <div className={styles.hours} role="group" aria-label="Hours to export">{hours.map((hour) => <label key={hour}>
            <input type="checkbox" aria-label={`Export forecast hour ${String(hour)}`} checked={selected.includes(hour)} onChange={(event) => {
              resetResult();
              setSelected(event.currentTarget.checked ? [...selected, hour] : selected.filter((value) => value !== hour));
            }} /><span>{forecastLabel(hour)}</span>
          </label>)}</div>
        </fieldset>
        <div className={styles.choices}>
          <SourceSelect label="Export resolution" value={width} options={SIZES} disabled={busy} onChange={(value) => { resetResult(); setWidth(value); }} />
          <SourceSelect label="Time per forecast hour" value={delay} disabled={busy} options={[{ value: 250, label: "0.25 seconds" }, { value: 500, label: "0.5 seconds" }, { value: 750, label: "0.75 seconds" }, { value: 1000, label: "1 second" }]} onChange={(value) => { resetResult(); setDelay(value); }} />
        </div>
      </div>
      <section className={styles.previewSection} aria-label="Forecast export preview">
        <div className={styles.preview} data-empty={progress === null && result === null}>
          {url !== null ? <img src={url} alt={`${context.model.label} ${product.label} exported forecast ${result?.format === "png" ? "image" : "animation"}`} /> : null}
          <canvas ref={preview} hidden={url !== null || progress === null || progress.phase === "loading"} aria-label="Export frame preview" />
          {url === null && (progress === null || progress.phase === "loading") ? <div className={styles.placeholder}><Film aria-hidden="true" /><strong>{busy ? activeFormat !== "png" ? "Loading every selected hour" : "Loading forecast hour" : "Your forecast export"}</strong><p>{busy ? "Capture begins after the complete selection is ready." : "Choose hours to animate, or create a PNG of the current hour."}</p></div> : null}
        </div>
        <div className={styles.previewCaption}><span>{width} × {height}</span><span>{activeFormat === "png" ? forecastLabel(context.hour) : `${String(selectedHours.length)} hours · ${(selectedHours.length * delay / 1000).toFixed(2)} s`}</span></div>
        <ol className={styles.stages} aria-label="Export stages">{(activeFormat !== "png" ? ["loading", "capturing", "encoding"] as const : ["loading", "capturing"] as const).map((stage, index) => <li key={stage} data-active={busy && progress?.phase === stage}><span>{index + 1}</span>{stage === "loading" ? "Load hours" : stage === "capturing" ? "Capture maps" : `Encode ${activeFormat.toUpperCase()}`}</li>)}</ol>
        <p className={styles.help}>WebP loops the selected hours with lossless, full-color frames. GIF also loops; desktop GIF copy supports up to 16 MiB. PNG captures the current hour.</p>
        {error === "" ? null : <Notice tone="danger" role="alert" title="Forecast export failed" message={error} />}
        {currentCopy?.state === "failed" ? <Notice tone="danger" role="alert" title={`${copyFormat} copy unavailable`} message={currentCopy.message ?? `Save the ${copyFormat} instead.`} /> : null}
      </section>
    </div>
    <footer className={styles.footer}>
      <div className={styles.status} role="status" aria-live="polite" aria-busy={busy || currentCopy?.state === "pending"}><span>{status}</span>{busy && progress !== null ? <progress aria-label={STAGES[progress.phase]} value={progress.done} max={progress.total} /> : null}</div>
      {busy ? <Action onClick={() => { controller.current?.abort(); }}>Cancel export</Action> : <>
        <Action onClick={close}>Close</Action>
        {url !== null && result !== null ? <a className={styles.save} href={url} download={result.filename}><Download className="icon-sm" aria-hidden="true" />Save {result.format.toUpperCase()}</a> : null}
        {result?.format === "gif" ? <Action disabled={currentCopy?.state === "pending"} onClick={() => { void copyGif(); }}><Copy className="icon-sm" aria-hidden="true" />Copy GIF</Action> : null}
        {result?.format === "png" ? <Action disabled={currentCopy?.state === "pending"} onClick={() => { void copyPng(); }}><Copy className="icon-sm" aria-hidden="true" />Copy PNG</Action> : null}
        <Action disabled={!available || !hours.includes(context.hour)} onClick={() => { start("png"); }}><Image className="icon-sm" aria-hidden="true" />Create PNG</Action>
        <Action disabled={selectedHours.length < 2 || !available} onClick={() => { start("gif"); }}><Film className="icon-sm" aria-hidden="true" />Create GIF</Action>
        <Action tone="accent" disabled={selectedHours.length < 2 || !available} onClick={() => { start("webp"); }}><Film className="icon-sm" aria-hidden="true" />Create WebP</Action>
      </>}
    </footer>
  </Popover>;
}

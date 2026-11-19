import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Copy, Download, Film, GripHorizontal, ListChecks, ListX, Pause, Play, X } from "lucide-react";
import { Popover } from "../../components/Popover";
import { Notice } from "../../components/Notice";
import { Action, IconAction } from "../../ui/Controls";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { cx } from "../../lib/cx";
import { useUiStore } from "../../stores/uiStore";
import type { ImageMode, RenderChoice, SoundingSummary } from "../../types/soundings";
import type { ViewRequest } from "../../types/soundingView";
import { encodeAnimation, type AnimationFormat } from "../animation/encodeAnimation";
import { copyForecastGif, useCanCopyGif } from "../forecastExport/forecastClipboard";
import { useModelCatalog, useObjectUrl } from "./soundingQueries";
import { SourceSelect } from "./SourceFields";
import { prepareSoundingAnimation, SOUNDING_ANIMATION_FRAME_LIMIT, soundingFrameChoices, type PreparedSoundings, type SoundingAnimationProgress } from "./prepareSoundingAnimation";
import styles from "../forecastExport/ForecastExportDialog.module.css";
import local from "./SoundingAnimationDialog.module.css";

const STAGES = { loading: "Loading profiles", scaling: "Measuring plot range", rendering: "Rendering soundings", encoding: "Encoding animation" };

export function SoundingAnimationDialog({ anchor, summary, choice, quality, view, onClose }: {
  anchor: HTMLButtonElement; summary: SoundingSummary; choice: RenderChoice; quality: ImageMode; view: ViewRequest; onClose: () => void;
}) {
  const client = useBackendClient();
  const canCopyGif = useCanCopyGif();
  const colorStyle = useUiStore((state) => state.soundingColorStyle);
  const models = useModelCatalog();
  const model = models.data?.models.find((item) => [item.key, item.label].some((name) => name.toLowerCase() === summary.model?.toLowerCase()));
  const choices = soundingFrameChoices(summary, model);
  const [selected, setSelected] = useState<number[] | null>(null);
  const startIndex = Math.max(0, choices.findIndex((item) => item.value === (summary.forecastHour ?? 0)));
  const chosen = selected ?? choices.slice(startIndex, startIndex + SOUNDING_ANIMATION_FRAME_LIMIT).map((item) => item.value);
  const maxFrames = Math.min(choices.length, SOUNDING_ANIMATION_FRAME_LIMIT);
  const selectAll = maxFrames === choices.length;
  const atLimit = !selectAll && chosen.length >= maxFrames;
  const frameLimit = String(SOUNDING_ANIMATION_FRAME_LIMIT);
  const [delay, setDelay] = useState(750);
  const [prepared, setPrepared] = useState<PreparedSoundings | null>(null);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<SoundingAnimationProgress | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [copying, setCopying] = useState(false);
  const [result, setResult] = useState<{ blob: Blob; filename: string; format: AnimationFormat } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const preview = useRef<HTMLCanvasElement>(null);
  const url = useObjectUrl(result?.blob ?? prepared?.frames[index]?.image);
  useEffect(() => () => { controller.current?.abort(); }, []);
  useEffect(() => {
    if (!playing || prepared === null) return;
    const timer = window.setInterval(() => { setIndex((current) => (current + 1) % prepared.frames.length); }, delay);
    return () => { window.clearInterval(timer); };
  }, [playing, prepared, delay]);
  const close = (): void => { controller.current?.abort(); onClose(); anchor.focus(); };
  const reset = (): void => { setPrepared(null); setResult(null); setProgress(null); setError(""); setCopied(false); setPlaying(false); setIndex(0); };
  const start = (format?: AnimationFormat): void => {
    if (busy || preview.current === null) return;
    const canvas = preview.current, job = new AbortController();
    controller.current = job; setBusy(true); setError(""); setResult(null); setCopied(false); setPlaying(false);
    const run = async (): Promise<void> => {
      const frames = prepared ?? await prepareSoundingAnimation({ summary, model, selected: choices.filter((item) => chosen.includes(item.value)),
        choice, quality, colorStyle, view }, client, job.signal, (value) => { if (!job.signal.aborted) setProgress(value); });
      job.signal.throwIfAborted(); setPrepared(frames); setIndex(0);
      if (format === undefined) { setPlaying(true); return; }
      setProgress({ phase: "encoding", done: 0, total: frames.frames.length });
      const blob = await encodeAnimation(frames.frames, frames.width, frames.height, format, delay, client, job.signal,
        (done) => { if (!job.signal.aborted) setProgress({ phase: "encoding", done, total: frames.frames.length }); }, canvas);
      job.signal.throwIfAborted();
      const label = (view.mode ?? "severe") === "winter" ? "winter" : "severe";
      const range = [frames.frames[0]?.label, frames.frames.at(-1)?.label].join("-");
      const filename = `sharppy-${summary.title}-${label}-${range}`.replace(/[^a-zA-Z0-9._-]+/g, "-").slice(0, 180) + `.${format}`;
      setResult({ blob, filename, format });
    };
    void run().catch((failure: unknown) => {
      if (!job.signal.aborted) setError(failure instanceof Error ? failure.message : "The sounding animation could not be prepared.");
    }).finally(() => {
      if (controller.current === job) { controller.current = null; setBusy(false); }
    });
  };
  const copy = (): void => {
    if (result?.format !== "gif" || copying) return;
    setCopying(true);
    void copyForecastGif(result.blob).then(() => { setCopied(true); }, () => { setError("This device could not copy the GIF. Save the GIF instead."); })
      .finally(() => { setCopying(false); });
  };
  const valid = chosen.length >= 2 && chosen.length <= SOUNDING_ANIMATION_FRAME_LIMIT;
  const exportReady = !busy && result !== null && url !== null;
  const formatLabel = result?.format === "webp" ? "WebP" : "GIF";
  const status = busy ? progress === null ? "Preparing…" : `${STAGES[progress.phase]} · ${String(progress.done)} / ${String(progress.total)}`
    : prepared !== null ? `${String(prepared.frames.length)} frames ready · ${String(prepared.width)} × ${String(prepared.height)}`
    : atLimit ? `${frameLimit} selected. Deselect a frame to choose another.`
    : `Choose 2–${frameLimit} frames to preview or export.`;
  return <Popover id="sounding-animation" open anchor={anchor} onClose={close} role="dialog" label="Animate sounding" initialFocus="container" movable backdrop className={styles.dialog}>
    <header className={styles.header} data-popover-drag>
      <IconAction icon={GripHorizontal} size="sm" label="Move sounding animation screen" data-popover-move />
      <div><h2>Animate sounding</h2><p>{summary.location ?? summary.title} · {view.mode === "winter" ? "Winter" : "Severe"}</p></div>
      <IconAction icon={X} size="sm" label="Close sounding animation" onClick={close} />
    </header>
    <div className={styles.body}>
      <div className={styles.settings}>
        <section className={styles.identity}><span>One location · One run</span><strong>{summary.title}</strong><p>Every selected profile loads before the images are rendered. Plot axes stay fixed throughout playback.</p></section>
        <hr className={local.separator} />
        <fieldset disabled={busy} className={local.framePicker}>
          <legend>{summary.kind === "model" ? "Forecast hours" : "File profiles"}</legend>
          <div className={local.framePanel}>
            <div className={local.frameToolbar}>
              <div className={local.frameActions}>
                <Action disabled={choices.length === 0} aria-label={selectAll ? "Select all frames" : `Select first ${frameLimit} frames`}
                  onClick={() => { reset(); setSelected(choices.slice(0, SOUNDING_ANIMATION_FRAME_LIMIT).map((item) => item.value)); }}>
                  <ListChecks className="icon-sm" aria-hidden="true" />{selectAll ? "Select all" : `First ${frameLimit}`}
                </Action>
                <Action disabled={chosen.length === 0} aria-label="Clear selected frames" onClick={() => { reset(); setSelected([]); }}>
                  <ListX className="icon-sm" aria-hidden="true" />Clear
                </Action>
              </div>
              <span className={local.frameCount} role="status">{chosen.length} / {maxFrames} selected</span>
            </div>
            {choices.length === 0 ? null : <div className={local.frameGrid} data-model={summary.kind === "model"} role="group" aria-label="Sounding frames to animate">
              {choices.map((item) => {
                const checked = chosen.includes(item.value);
                return <label className={local.frameOption} key={item.value}>
                  <input type="checkbox" aria-label={`Animate ${item.label}`} checked={checked} disabled={atLimit && !checked}
                    onChange={(event) => {
                      reset(); setSelected(event.currentTarget.checked ? [...chosen, item.value] : chosen.filter((value) => value !== item.value));
                    }} />
                  <span className={local.frameTile}>
                    <strong>{item.timeLabel === undefined ? item.label : `Profile ${String(item.value + 1)}`}</strong>
                    {item.timeLabel === undefined ? null : <small>{item.timeLabel}</small>}
                  </span>
                </label>;
              })}
            </div>}
          </div>
        </fieldset>
        {summary.kind === "model" && models.isPending ? <p className={styles.help}>Loading available forecast hours…</p> : null}
        {models.isError && summary.kind === "model" ? <Notice tone="danger" title="Forecast hours unavailable" message={models.error.message} /> : null}
        <hr className={local.separator} />
        <SourceSelect label="Time per sounding" value={delay} disabled={busy} options={[{ value: 250, label: "0.25 seconds" }, { value: 500, label: "0.5 seconds" }, { value: 750, label: "0.75 seconds" }, { value: 1000, label: "1 second" }]}
          onChange={(value) => { setResult(null); setCopied(false); setDelay(value); }} />
        <p className={styles.help}>{quality === "uhd" ? "UHD" : "HD"} · Original rendered PNG frames · Looping playback with discrete forecast hours.</p>
      </div>
      <section className={styles.previewSection} aria-label="Sounding animation preview">
        <div className={cx(styles.preview, local.preview)} style={{ "--animation-aspect": prepared === null ? "16 / 9" : `${String(prepared.width)} / ${String(prepared.height)}` } as CSSProperties}>
          {url === null ? <div className={styles.placeholder}><Film aria-hidden="true" /><strong>Your sounding animation</strong><p>Prepare frames to play every selected hour.</p></div> : <img src={url} alt="Selected sounding animation frame" />}
          <canvas ref={preview} hidden aria-label="Sounding encoding preview" />
        </div>
        {prepared === null || result !== null ? null : <div className={local.playback}>
          <Action disabled={busy} onClick={() => { setPlaying(!playing); }}>{playing ? <Pause className="icon-sm" /> : <Play className="icon-sm" />}{playing ? "Pause" : "Play"}</Action>
          <input aria-label="Sounding animation frame" type="range" min={0} max={prepared.frames.length - 1} value={index} disabled={busy}
            onChange={(event) => { setPlaying(false); setIndex(Number(event.currentTarget.value)); }} />
          <span>{prepared.frames[index]?.label}</span>
        </div>}
        <p className={styles.help}>Use GIF for sharing or WebP to keep the full colors without loss. Each hour uses its saved profile edits and the selected Severe or Winter layout.</p>
        {error === "" ? null : <Notice tone="danger" role="alert" title="Sounding animation unavailable" message={error} />}
      </section>
    </div>
    <footer className={local.footer} data-export-ready={exportReady}>
      <div className={exportReady ? local.resultBar : undefined}>
        <div className={exportReady ? local.resultSummary : styles.status} role="status" aria-busy={busy || copying}>
          {exportReady ? <>
            <span className={local.resultIcon}><Film className="icon" aria-hidden="true" /></span>
            <div>
              <strong>{result.format === "gif" && copied ? "GIF copied" : `${formatLabel} ready`}</strong>
              <span className={local.resultSize}>{(result.blob.size / 1024 ** 2).toFixed(2)} MiB</span>
            </div>
          </> : <>
            <span>{status}</span>{busy && progress !== null ? <progress aria-label={STAGES[progress.phase]} value={progress.done} max={progress.total} /> : null}
          </>}
        </div>
        {exportReady ? <div className={local.resultActions} role="group" aria-label="Finished animation">
          {result.format === "gif" && canCopyGif ? <Action disabled={copying} onClick={copy}><Copy className="icon-sm" aria-hidden="true" />{copying ? "Copying…" : "Copy GIF"}</Action> : null}
          <a className={styles.save} href={url} download={result.filename}><Download className="icon-sm" aria-hidden="true" />Save {formatLabel}</a>
        </div> : null}
      </div>
      <div className={local.actionBar}>
        {busy ? <Action onClick={() => { controller.current?.abort(); }}>Cancel</Action> : <>
          <div className={local.previewActions}>
            <Action onClick={close}>Close</Action>
            <Action disabled={!valid} onClick={() => { if (prepared === null) start(); else { setResult(null); setPlaying(true); } }}><Play className="icon-sm" aria-hidden="true" />Prepare playback</Action>
          </div>
          <div className={local.createActions}>
            <Action disabled={!valid} onClick={() => { start("webp"); }}>Create WebP</Action>
            <Action tone="accent" disabled={!valid} onClick={() => { start("gif"); }}><Film className="icon-sm" aria-hidden="true" />Create GIF</Action>
          </div>
        </>}
      </div>
    </footer>
  </Popover>;
}

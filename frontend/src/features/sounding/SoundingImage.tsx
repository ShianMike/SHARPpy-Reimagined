import { Copy, Download, Hand, Maximize, Pencil, Redo2, RotateCcw, Undo2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type PointerEvent, type ReactNode } from "react";
import { Action, IconAction } from "../../ui/Controls";
import type { ImageMode, RenderChoice } from "../../types/soundings";
import { AscentPicker } from "./AscentPicker";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { useObjectUrl } from "./soundingQueries";
import { PointEditor } from "./PointEditor";
import { SourceSelect } from "./SourceFields";
import { useSoundingView } from "./useSoundingView";
import { replaceValues } from "./viewHistory";
import styles from "./Viewer.module.css";

export function SoundingImage({ soundingId, choice, title, heading }: { soundingId: string; choice: RenderChoice; title: string; heading: ReactNode }): ReactNode {
  const [quality, setQuality] = useState<ImageMode>("uhd");
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ image: Blob; text: string } | null>(null);
  const [zoom, setZoom] = useState<number | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [preview, setPreview] = useState<{ id: string; x: number; y: number } | null>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ id: string | null; x: number; y: number; left: number; top: number } | null>(null);
  const { image, edit, state, edits, request, busy, update } = useSoundingView(soundingId, choice, quality);
  const data = image.data;
  const unavailable = busy || image.isError;
  const message = notice?.image === data?.image ? notice?.text : undefined;
  const url = useObjectUrl(data?.image);
  const point = data?.points.find((point) => point.id === selected);
  const ratio = window.devicePixelRatio || 1;
  const fit = data === undefined ? 1 : Math.min(1 / ratio, Math.max(1, size.width - 24) / data.width, Math.max(1, size.height - 24) / data.height);
  const scale = zoom ?? fit;
  const zoomTo = (value: number): void => { setZoom(Math.max(0.1, Math.min(2 / ratio, value))); };
  const undo = (): void => { if (!busy && state.cursor > 0) update({ ...state, cursor: state.cursor - 1 }); };
  const redo = (): void => { if (!busy && state.cursor < state.history.length - 1) update({ ...state, cursor: state.cursor + 1 }); };

  useEffect(() => {
    const element = viewport.current;
    if (element === null) return;
    const measure = (): void => {
      const box = element.getBoundingClientRect();
      setSize({ width: Math.floor(box.width), height: Math.floor(box.height) });
    };
    let frame: number | null = null;
    const observer = new ResizeObserver(() => {
      frame ??= requestAnimationFrame(() => { frame = null; measure(); });
    });
    measure();
    // Image scrollbars must not change the box used to fit that same image.
    observer.observe(element, { box: "border-box" });
    return () => { observer.disconnect(); if (frame !== null) cancelAnimationFrame(frame); };
  }, []);

  const exportImage = async (copy: boolean): Promise<void> => {
    if (data === undefined || url === null || unavailable) return;
    try {
      if (copy) {
        if (!("ClipboardItem" in window) || !("clipboard" in navigator)) throw new Error("unavailable");
        await navigator.clipboard.write([new ClipboardItem({ "image/png": data.image })]);
        setNotice({ image: data.image, text: "Full-resolution image copied." });
      } else {
        const link = document.createElement("a");
        link.href = url; link.download = title.replace(/[^a-zA-Z0-9_-]+/g, "-") + "-" + choice + (edits.length ? "-edited" : "") + ".png";
        link.click(); setNotice({ image: data.image, text: "Full-resolution PNG exported." });
      }
    } catch { setNotice({ image: data.image, text: "Clipboard access was unavailable. Use Export PNG to save the image." }); }
  };
  const start = (event: PointerEvent<HTMLDivElement>): void => {
    if (event.button !== 0 || data === undefined || unavailable || viewport.current === null) return;
    const rect = frame.current?.getBoundingClientRect();
    if (rect === undefined) return;
    const nearest = editing ? data.points.reduce<{ id: string | null; distance: number }>((best, point) => {
      const distance = Math.hypot(point.x * rect.width + rect.left - event.clientX, point.y * rect.height + rect.top - event.clientY);
      return distance < best.distance ? { id: point.id, distance } : best;
    }, { id: null, distance: 16 }).id : null;
    if (editing && nearest === null) return;
    if (nearest !== null) setSelected(nearest);
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { id: nearest, x: event.clientX, y: event.clientY, left: viewport.current.scrollLeft, top: viewport.current.scrollTop };
  };
  const move = (event: PointerEvent<HTMLDivElement>): void => {
    const active = gesture.current;
    if (active === null || viewport.current === null) return;
    if (active.id === null) {
      viewport.current.scrollLeft = active.left - event.clientX + active.x;
      viewport.current.scrollTop = active.top - event.clientY + active.y;
    } else {
      const rect = frame.current?.getBoundingClientRect();
      if (rect !== undefined) setPreview({ id: active.id, x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) });
    }
  };
  const finish = (event: PointerEvent<HTMLDivElement>): void => {
    const active = gesture.current; gesture.current = null; setPreview(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (active?.id && preview !== null && Math.hypot(event.clientX - active.x, event.clientY - active.y) > 3) {
      edit.mutate({ ...request, drag: { target: active.id, x: preview.x, y: preview.y } });
    }
  };
  return <div className={styles.imageViewer} onKeyDown={(event) => {
    if ((event.target as HTMLElement).closest("input,select,textarea,[role=combobox],[role=listbox]")) return;
    if (event.key === "Escape") { setEditing(false); setSelected(null); }
    if ((event.ctrlKey || event.metaKey) && ["z", "y"].includes(event.key.toLowerCase())) {
      event.preventDefault(); if (event.shiftKey || event.key.toLowerCase() === "y") redo(); else undo();
    }
  }}>
    <header className={styles.header} tabIndex={0} aria-label="Sounding details">
      {heading}
      <div className={styles.imageMeta}>
        <span>{edits.length ? String(edits.length) + " edited levels · Original retained" : "Original profile"} · {editing ? "Drag points · Ctrl+Z to undo" : "Zoom for detail · Drag to pan"}</span>
        <span role="status">{message || (data === undefined ? "" : String(data.width) + " × " + String(data.height) + " · lossless PNG")}</span>
      </div>
    </header>
    <div className={styles.tools} role="toolbar" aria-label="Sounding image controls">
      <div className={styles.toolGroup}>
        <Action aria-pressed={!editing} onClick={() => { setEditing(false); setSelected(null); }}><Hand className="icon-sm" />Inspect</Action>
        <Action aria-pressed={editing} onClick={() => { setEditing(true); }} disabled={data === undefined || unavailable}><Pencil className="icon-sm" />Edit profile</Action>
        <IconAction icon={Undo2} label="Undo edit" size="sm" disabled={busy || state.cursor === 0} onClick={undo} />
        <IconAction icon={Redo2} label="Redo edit" size="sm" disabled={busy || state.cursor >= state.history.length - 1} onClick={redo} />
        <IconAction icon={RotateCcw} label="Reset profile" size="sm" disabled={busy || edits.length === 0} onClick={() => { edit.mutate({ ...request, edits: [] }); }} />
      </div>
      <div className={styles.toolGroup}>
        <IconAction icon={ZoomOut} label="Zoom out" size="sm" onClick={() => { zoomTo(scale / 1.25); }} />
        <button className={styles.zoomLabel} type="button" onClick={() => { zoomTo(1 / ratio); }} aria-label="Actual pixels (100%)" title="Actual pixels (100%)">{Math.round(scale * ratio * 100)}%</button>
        <IconAction icon={ZoomIn} label="Zoom in" size="sm" onClick={() => { zoomTo(scale * 1.25); }} />
        <IconAction icon={Maximize} label="Fit sounding to view" size="sm" aria-pressed={zoom === null} onClick={() => { setZoom(null); }} />
        <SourceSelect<ImageMode> label="Image resolution" value={quality} disabled={busy} compact options={[{ value: "uhd", label: "UHD · lossless" }, { value: "hd", label: "HD · lossless" }]} onChange={setQuality} />
      </div>
      <div className={styles.exportTools}>
        {data === undefined ? null : <details className={styles.panelPicker}>
          <summary>Chart panels</summary>
          <div className={styles.options} data-motion="enter">
            <strong>Chart panels</strong>
            <AscentPicker />
            {data.options.map((group) => <SourceSelect key={group.key} label={group.label} value={state.options[group.key] ?? data.selection[group.key]} disabled={busy}
              options={group.choices.map((option) => ({ value: option.key, label: option.label }))}
              onChange={(value) => { update({ ...state, options: { ...state.options, [group.key]: value } }); }} />)}
          </div>
        </details>}
        <IconAction icon={Copy} label="Copy sounding image" size="sm" disabled={data === undefined || unavailable} onClick={() => { void exportImage(true); }} />
        <Action disabled={data === undefined || unavailable} onClick={() => { void exportImage(false); }}><Download className="icon-sm" />Export PNG</Action>
      </div>
    </div>
    {editing ? <div className={styles.editBar} data-motion="fade"><Pencil className="icon-sm" /><span>Drag a highlighted point, or choose a level to enter values.</span>
      <SourceSelect label="Profile level" value={selected ?? ""} disabled={busy} compact options={[{ value: "", label: "Choose a level…" }, ...(data?.points.map((point) => ({ value: point.id, label: point.label })) ?? [])]} onChange={(value) => { setSelected(value || null); }} />
      <Action onClick={() => { setEditing(false); setSelected(null); }}>Done</Action>
    </div> : null}
    <div className={styles.stage} data-testid="sounding-stage" aria-busy={busy}>
      <div ref={viewport} className={styles.viewport} tabIndex={0} role="region" aria-label="Sounding image. Use zoom controls, then drag to pan.">
        <div className={styles.canvasSpace}>
          {url !== null && data !== undefined ? <div ref={frame} className={styles.imageFrame} data-editing={editing}
            style={{ "--image-width": String(Math.floor(data.width * scale)) + "px", "--image-height": String(Math.floor(data.height * scale)) + "px" } as CSSProperties}
            onPointerDown={start} onPointerMove={move} onPointerUp={finish} onPointerCancel={() => { gesture.current = null; setPreview(null); }}>
            <img className={styles.image} src={url} alt={"Sounding: " + title} draggable={false} data-testid="sounding-image" />
            {editing ? <svg className={styles.editOverlay} viewBox={"0 0 " + String(data.width) + " " + String(data.height)} aria-hidden="true">
              {data.points.map((point) => <circle key={point.id} cx={point.x * data.width} cy={point.y * data.height} r={4 / scale} data-selected={point.id === selected}><title>{point.label}: {point.values.map((v) => v.value.toFixed(1) + " " + v.unit).join(", ")}</title></circle>)}
              {preview === null ? null : <circle cx={preview.x * data.width} cy={preview.y * data.height} r={7 / scale} data-selected="true" />}
            </svg> : null}
          </div> : null}
        </div>
      </div>
      {editing && point !== undefined ? <PointEditor key={point.id + JSON.stringify(point.values)} point={point} busy={unavailable} onClose={() => { setSelected(null); }} onApply={(values) => { edit.mutate({ ...request, edits: replaceValues(edits, Number(point.id.split(":")[1]), values) }); }} /> : null}
      {busy ? <div className={styles.rendering} data-motion="fade" role="status">{data === undefined ? "Drawing high-resolution sounding…" : edit.isPending ? "Recalculating profile…" : "Updating sounding…"}</div> : null}
      {image.isError || edit.isError ? <div className={styles.failure}><SoundingErrorNotice error={edit.error ?? image.error} operation="render_sounding" onRetry={() => { if (edit.variables) edit.mutate(edit.variables); else void image.refetch(); }} busy={busy} testId="render-error" /></div> : null}
    </div>
  </div>;
}

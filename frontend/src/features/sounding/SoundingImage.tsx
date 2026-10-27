import { ChevronDown, Hand, Maximize, MoveHorizontal, Pencil, Redo2, RotateCcw, Undo2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Action, IconAction } from "../../ui/Controls";
import type { ImageMode, RenderChoice } from "../../types/soundings";
import { convertUnit, displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { useUpdateStore } from "../../stores/updateStore";
import { AscentPicker } from "./AscentPicker";
import { SoundingErrorNotice } from "./SoundingErrorNotice";
import { SoundingExport } from "./SoundingExport";
import { useObjectUrl } from "./soundingQueries";
import { PointEditor } from "./PointEditor";
import { LevelReadout } from "./LevelReadout";
import { QuickUnits } from "../settings/QuickUnits";
import { SourceSelect } from "./SourceFields";
import { useSoundingView } from "./useSoundingView";
import { replaceValues } from "./viewHistory";
import styles from "./Viewer.module.css";

export function SoundingImage({ soundingId, choice, title, heading, navigation, controlsTarget, infoTarget, timeIndex = 0 }: { soundingId: string; choice: RenderChoice; title: string; heading: ReactNode; navigation?: ReactNode; controlsTarget?: HTMLDivElement | null; infoTarget?: HTMLDivElement | null; timeIndex?: number }): ReactNode {
  const units = useUiStore((state) => state.units);
  const [quality, setQuality] = useState<ImageMode>("uhd");
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [zoom, setZoom] = useState<number | null>(null);
  const [fitWidth, setFitWidth] = useState(true);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [preview, setPreview] = useState<{ id: string; x: number; y: number } | null>(null);
  const [inspected, setInspected] = useState<{ image: Blob; index: number; x: number; y: number; chart: "skew" | "hodograph"; box: DOMRect } | null>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const inspectButton = useRef<HTMLButtonElement>(null);
  const gesture = useRef<{ id: string | null; x: number; y: number; left: number; top: number } | null>(null);
  const { image, edit, state, edits, request, busy, ready, update } = useSoundingView(soundingId, choice, quality, timeIndex);
  const data = image.data;
  const editedLevels = useMemo(() => {
    const indices = new Set(edits.map((edit) => String(edit.index)));
    return new Set(data?.points.filter((point) => indices.has(point.id.split(":")[1] ?? "") && point.values.some((field) => field.original !== null && field.value !== field.original)).map((point) => point.id.split(":")[1]));
  }, [data?.points, edits]);
  const pointTitles = useMemo(() => editing ? data?.points.map((point) => `${point.label}: ${point.values.map((v) => {
    const unit = displayUnit(v.unit, units);
    return convertUnit(v.value, v.unit, unit).toFixed(1) + " " + unit;
  }).join(", ")}`) : [], [data?.points, units, editing]);
  const unavailable = busy || image.isError;
  const levelIndex = inspected?.image === data?.image ? inspected?.index ?? 0 : 0;
  const level = data?.inspection.levels[levelIndex];
  const windLevels = useMemo(() => data?.inspection.levels.filter((level) => level.hodograph !== null) ?? [], [data?.inspection.levels]);
  const windIndex = Math.max(0, windLevels.findIndex((wind) => wind.index === level?.index));
  const windLevel = windLevels[windIndex];
  const inspecting = !editing && !unavailable && inspected !== null && inspected.image === data?.image && level !== undefined;
  const readings = useMemo<[string, string][]>(() => {
    if (level === undefined || editing) return [];
    const reading = (value: number | null, source: string): string => {
      const unit = displayUnit(source, units);
      return value === null ? "Unavailable" : `${convertUnit(value, source, unit).toFixed(1)} ${unit}`;
    };
    return [
      ["Pressure", `${level.pressure.toFixed(1)} hPa`], ["Height", level.height === null ? "Unavailable" : `${String(Math.round(level.height))} m MSL`],
      ["Temperature", reading(level.tmpc, "°C")], ["Dewpoint", reading(level.dwpc, "°C")],
      ["Wind", level.windSpeed === 0 ? "Calm" : level.windDirection === null || level.windSpeed === null ? "Unavailable" : `${String(Math.round(level.windDirection)).padStart(3, "0")}° / ${reading(level.windSpeed, "kt")}`],
    ];
  }, [level, units, editing]);
  const inspect = (index: number, position?: { x: number; y: number }, chart: "skew" | "hodograph" = "skew"): void => {
    if (data === undefined || unavailable || editing) return;
    const rect = frame.current?.getBoundingClientRect();
    const level = data.inspection.levels[index];
    if (rect === undefined || level === undefined) return;
    const { x, y } = position ?? { x: rect.left + (chart === "hodograph" ? level.hodograph?.x ?? 0 : data.inspection.bounds.right) * rect.width,
      y: rect.top + (chart === "hodograph" ? level.hodograph?.y ?? 0 : level.y) * rect.height };
    setInspected((previous) => previous?.image === data.image && previous.index === index && previous.x === x && previous.y === y && previous.chart === chart ? previous : { image: data.image, index, x, y, chart, box: rect });
  };
  const url = useObjectUrl(data?.image);
  const point = data?.points.find((point) => point.id === selected);
  const ratio = window.devicePixelRatio || 1;
  const fit = data === undefined ? 1 : Math.min(1 / ratio, Math.max(1, size.width - 24) / data.width, fitWidth ? Infinity : Math.max(1, size.height - 24) / data.height);
  const scale = zoom ?? fit;
  const box = inspected?.box;
  const linkedPosition = (chart: "skew" | "hodograph"): { x: number; y: number } => {
    if (inspected?.chart === chart) return inspected;
    const point = chart === "skew" ? data?.points.find((point) => point.id === `tmpc:${String(level?.index)}` || point.id === `dwpc:${String(level?.index)}`) : level?.hodograph;
    return { x: (box?.left ?? 0) + (point?.x ?? (chart === "skew" ? data?.inspection.bounds.right ?? 0 : 0.75)) * (box?.width ?? 0),
      y: (box?.top ?? 0) + (chart === "skew" ? level?.y ?? 0 : point?.y ?? data?.inspection.bounds.top ?? 0) * (box?.height ?? 0) };
  };
  const zoomTo = (value: number): void => { setZoom(Math.max(0.1, Math.min(2 / ratio, value))); };
  const undo = (): void => { if (!busy && state.cursor > 0) update({ ...state, cursor: state.cursor - 1 }); };
  const redo = (): void => { if (!busy && state.cursor < state.history.length - 1) update({ ...state, cursor: state.cursor + 1 }); };

  useEffect(() => {
    const element = viewport.current;
    if (element === null) return;
    const measure = (): void => {
      setInspected(null);
      setSize({ width: element.clientWidth, height: element.clientHeight });
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

  useEffect(() => {
    if (!inspecting) return;
    const dismiss = (event: KeyboardEvent): void => { if (event.key === "Escape") setInspected(null); };
    window.addEventListener("keydown", dismiss);
    return () => { window.removeEventListener("keydown", dismiss); };
  }, [inspecting]);

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
    setInspected(null);
    event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { id: nearest, x: event.clientX, y: event.clientY, left: viewport.current.scrollLeft, top: viewport.current.scrollTop };
  };
  const move = (event: PointerEvent<HTMLDivElement>): void => {
    const active = gesture.current;
    if (active === null) {
      const rect = frame.current?.getBoundingClientRect();
      if (rect === undefined || data === undefined || unavailable || editing || event.buttons !== 0) return;
      const x = (event.clientX - rect.left) / rect.width, y = (event.clientY - rect.top) / rect.height;
      const { bounds, levels } = data.inspection;
      const skew = x >= bounds.left && x <= bounds.right && y >= bounds.top && y <= bounds.bottom;
      const nearest = levels.reduce((best, level, index) => {
        const distance = skew ? Math.abs(level.y - y) : level.hodograph === null ? Infinity
          : Math.hypot((level.hodograph.x - x) * rect.width, (level.hodograph.y - y) * rect.height);
        return distance < best.distance ? { index, distance } : best;
      }, { index: -1, distance: skew ? Infinity : 18 });
      if (nearest.index < 0) setInspected(null);
      else inspect(nearest.index, { x: event.clientX, y: event.clientY }, skew ? "skew" : "hodograph");
      return;
    }
    if (viewport.current === null) return;
    if (active.id === null) {
      viewport.current.scrollLeft = active.left - event.clientX + active.x;
      viewport.current.scrollTop = active.top - event.clientY + active.y;
    } else {
      const rect = frame.current?.getBoundingClientRect();
      if (Math.hypot(event.clientX - active.x, event.clientY - active.y) > 3) useUpdateStore.getState().noteSoundingWork();
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
  const editor = editing && point !== undefined ? <PointEditor key={point.id + JSON.stringify(point.values) + JSON.stringify(units)} point={point} busy={unavailable} onApply={(values) => { edit.mutate({ ...request, edits: replaceValues(edits, Number(point.id.split(":")[1]), values) }); }} /> : null;
  const info = <header className={styles.header} data-docked={infoTarget !== undefined} tabIndex={0} aria-label="Sounding details">
    {infoTarget === undefined ? null : <strong className={styles.groupLabel}>Info</strong>}
    {heading}
    <div className={styles.imageMeta}>
      <span>{edits.length ? `${String(edits.length)} edited ${edits.length === 1 ? "level" : "levels"} · Original retained` : "Original profile"}</span>
      <span role="status">{data === undefined ? "" : String(data.width) + " × " + String(data.height) + " · lossless PNG"}</span>
    </div>
  </header>;
  const controls = <>
    <div className={styles.tools} data-docked={controlsTarget !== undefined} role="toolbar" aria-label="Sounding image controls">
      <div className={styles.toolGroup} role="group" aria-label="Profile controls">
        <span className={styles.groupLabel}>Profile</span>
        <div className={styles.profileMode}>
          <Action ref={inspectButton} aria-pressed={!editing} onClick={() => { setEditing(false); setSelected(null); }}><Hand className="icon-sm" />Inspect</Action>
          <Action aria-pressed={editing} onClick={() => { setEditing(true); }} disabled={data === undefined || unavailable}><Pencil className="icon-sm" />Edit profile</Action>
        </div>
        {state.history.length < 2 ? null : <><SourceSelect label="Undo history" value={state.cursor} disabled={busy}
          options={state.history.map((snapshot, index) => ({ value: index, label: snapshot.length === 0 ? index === 0 ? "Original profile" : `${String(index)} · Reset profile`
            : `${String(index)} · ${String(snapshot.length)} edited ${snapshot.length === 1 ? "level" : "levels"}` }))}
          onChange={(cursor) => { update({ ...state, cursor }); }} />
        <div className={styles.historyActions}>
          <Action aria-label="Undo edit" disabled={busy || state.cursor === 0} onClick={undo}><Undo2 className="icon-sm" />Undo</Action>
          <Action aria-label="Redo edit" disabled={busy || state.cursor >= state.history.length - 1} onClick={redo}><Redo2 className="icon-sm" />Redo</Action>
          <Action aria-label="Reset profile" disabled={busy || edits.length === 0} onClick={() => { edit.mutate({ ...request, edits: [] }); }}><RotateCcw className="icon-sm" />Reset</Action>
        </div></>}
      </div>
      {editing ? <section className={styles.levelEditor} aria-label="Level editor">
        <header><strong>Edit level</strong><Action aria-label="Finish editing" onClick={() => { setEditing(false); setSelected(null); inspectButton.current?.focus(); }}>Done</Action></header>
        <SourceSelect label="Profile level" value={selected ?? ""} disabled={busy} compact options={[{ value: "", label: "Choose a level…" }, ...(data?.points.map((point) => ({ value: point.id, label: point.label + (editedLevels.has(point.id.split(":")[1]) ? " · Edited" : "") })) ?? [])]} onChange={(value) => { setSelected(value || null); }} />
        {editor ?? <p>Choose a level or drag a point.</p>}
      </section> : null}
      <details className={styles.viewControls} aria-label="View controls" open={!editing}>
        <summary>View<ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        <div className={styles.viewBody}>
        <div className={styles.viewActions}><div className={styles.zoomControls}>
          <IconAction icon={ZoomOut} label="Zoom out" size="sm" onClick={() => { zoomTo(scale / 1.25); }} />
          <button className={styles.zoomLabel} type="button" onClick={() => { zoomTo(1 / ratio); }} aria-label="Actual pixels (100%)" title="Actual pixels (100%)">{Math.round(scale * ratio * 100)}%</button>
          <IconAction icon={ZoomIn} label="Zoom in" size="sm" onClick={() => { zoomTo(scale * 1.25); }} />
        </div><QuickUnits /></div>
        <div className={styles.fitControls}>
          <Action aria-label="Fit sounding to width" aria-pressed={zoom === null && fitWidth} onClick={() => { setFitWidth(true); setZoom(null); }}><MoveHorizontal className="icon-sm" />Fit width</Action>
          <Action aria-label="Fit sounding to view" aria-pressed={zoom === null && !fitWidth} onClick={() => { setFitWidth(false); setZoom(null); }}><Maximize className="icon-sm" />Fit all</Action>
        </div>
        {data === undefined ? null : <details className={styles.panelPicker}>
          <summary>Chart panels<ChevronDown className="icon-sm" aria-hidden="true" /></summary>
          <div className={styles.options} data-motion="enter">
            <AscentPicker />
            {data.options.map((group) => <SourceSelect key={group.key} label={group.label} value={state.options[group.key] ?? data.selection[group.key]} disabled={busy || (group.key === "chart" && (state.options.panel ?? data.selection.panel) === "HEIGHT CHARTS")}
              options={group.choices.map((option) => ({ value: option.key, label: option.label }))}
              onChange={(value) => { update({ ...state, options: { ...state.options, [group.key]: value } }); }} />)}
          </div>
        </details>}
        </div>
      </details>
      <div className={styles.exportTools}>
        <SoundingExport view={data} quality={quality} onQualityChange={setQuality} ready={ready} busy={busy}
          filename={title.replace(/[^a-zA-Z0-9_-]+/g, "-") + "-" + choice + (timeIndex > 0 ? `-time-${String(timeIndex + 1)}` : "") + (edits.length ? "-edited" : "") + ".png"} />
      </div>
    </div>
  </>;
  return <div className={styles.imageViewer} onKeyDown={(event) => {
    if ((event.target as HTMLElement).closest("input,select,textarea,[role=combobox],[role=listbox]")) return;
    if (event.key === "Escape") { setEditing(false); setSelected(null); setInspected(null); }
    if ((event.ctrlKey || event.metaKey) && ["z", "y"].includes(event.key.toLowerCase())) {
      event.preventDefault(); if (event.shiftKey || event.key.toLowerCase() === "y") redo(); else undo();
    }
  }}>
    {infoTarget === undefined ? info : infoTarget === null ? null : createPortal(info, infoTarget)}
    {navigation == null ? null : <div className={styles.showSidebar}>{navigation}</div>}
    {controlsTarget === undefined ? controls : controlsTarget === null ? null : createPortal(controls, controlsTarget)}
    <div className={styles.stage} data-testid="sounding-stage" aria-busy={busy}>
      <div ref={viewport} className={styles.viewport} tabIndex={0} role="region" aria-label="Sounding image. Use zoom controls, then drag to pan." onScroll={() => { setInspected(null); }}>
        <div className={styles.canvasSpace}>
          {url !== null && data !== undefined ? <div ref={frame} className={styles.imageFrame} data-editing={editing}
            style={{ "--image-width": String(Math.floor(data.width * scale)) + "px", "--image-height": String(Math.floor(data.height * scale)) + "px" } as CSSProperties}
            onPointerDown={start} onPointerMove={move} onPointerLeave={(event) => { if (!(event.relatedTarget instanceof Element) || event.relatedTarget.closest("[data-sounding-level]") === null) setInspected(null); }} onPointerUp={finish} onPointerCancel={() => { gesture.current = null; setPreview(null); setInspected(null); }}>
            <img className={styles.image} src={url} alt={"Sounding: " + title} draggable={false} data-testid="sounding-image" />
            {editing || unavailable || data.inspection.levels.length === 0 ? null : <svg className={styles.inspectionOverlay} viewBox={`0 0 ${String(data.width)} ${String(data.height)}`}>
              <rect x={data.inspection.bounds.left * data.width} y={data.inspection.bounds.top * data.height}
                width={(data.inspection.bounds.right - data.inspection.bounds.left) * data.width} height={(data.inspection.bounds.bottom - data.inspection.bounds.top) * data.height}
                role="slider" tabIndex={0} aria-label="Skew-T inspection level" aria-orientation="vertical" aria-valuemin={0} aria-valuemax={data.inspection.levels.length - 1}
                aria-valuenow={levelIndex} aria-valuetext={readings.map(([label, value]) => `${label}: ${value}`).join("; ")}
                onFocus={() => { inspect(levelIndex); }} onBlur={() => { setInspected(null); }} onKeyDown={(event) => {
                  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  inspect(event.key === "Home" ? 0 : event.key === "End" ? data.inspection.levels.length - 1 : Math.max(0, Math.min(data.inspection.levels.length - 1, levelIndex + (event.key === "ArrowUp" ? 1 : -1))));
                }} />
              {windLevel?.hodograph == null ? null : <circle className={styles.windCursor} cx={windLevel.hodograph.x * data.width} cy={windLevel.hodograph.y * data.height} r={12 / scale}
                role="slider" tabIndex={0} aria-label="Hodograph inspection level" aria-valuemin={0} aria-valuemax={windLevels.length - 1} aria-valuenow={windIndex}
                aria-valuetext={readings.filter((_, index) => index === 0 || index === 1 || index === 4).map(([label, value]) => `${label}: ${value}`).join("; ")}
                onFocus={() => { inspect(data.inspection.levels.indexOf(windLevel), undefined, "hodograph"); }} onBlur={() => { setInspected(null); }} onKeyDown={(event) => {
                  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  const next = windLevels[event.key === "Home" ? 0 : event.key === "End" ? windLevels.length - 1 : Math.max(0, Math.min(windLevels.length - 1, windIndex + (event.key === "ArrowUp" ? 1 : -1)))];
                  if (next !== undefined) inspect(data.inspection.levels.indexOf(next), undefined, "hodograph");
                }} />}
              {!inspecting ? null : <g aria-hidden="true" data-testid="level-highlights">
                <line x1={data.inspection.bounds.left * data.width} x2={data.inspection.bounds.right * data.width} y1={level.y * data.height} y2={level.y * data.height} />
                {data.points.filter((point) => point.id === `tmpc:${String(level.index)}` || point.id === `dwpc:${String(level.index)}`).map((point) => <circle key={point.id} cx={point.x * data.width} cy={point.y * data.height} r={4 / scale} />)}
                {level.hodograph === null ? null : <circle data-kind="hodograph" cx={level.hodograph.x * data.width} cy={level.hodograph.y * data.height} r={8 / scale} />}
              </g>}
            </svg>}
            {editing ? <svg className={styles.editOverlay} viewBox={"0 0 " + String(data.width) + " " + String(data.height)} aria-hidden="true">
              {data.points.map((point, index) => <circle key={point.id} cx={point.x * data.width} cy={point.y * data.height} r={(editedLevels.has(point.id.split(":")[1]) ? 6 : 4) / scale} data-edited={editedLevels.has(point.id.split(":")[1])} data-selected={point.id === selected}><title>{pointTitles?.[index]}{editedLevels.has(point.id.split(":")[1]) ? " · Edited level" : ""}</title></circle>)}
              {preview === null ? null : <circle cx={preview.x * data.width} cy={preview.y * data.height} r={7 / scale} data-selected="true" />}
            </svg> : null}
          </div> : null}
        </div>
      </div>
      {busy ? <div className={styles.rendering} data-motion="fade" role="status">{data === undefined ? "Rendering sounding image…" : edit.isPending ? "Recalculating profile…" : "Rendering updated sounding…"}</div> : null}
      {image.isError || edit.isError ? <div className={styles.failure} data-retained={data !== undefined}><div className={styles.failureCard}><SoundingErrorNotice error={edit.error ?? image.error} operation="render_sounding_view" onRetry={() => { if (edit.variables) edit.mutate(edit.variables); else void image.refetch(); }} busy={busy} testId="render-error" /></div></div> : null}
    </div>
    {!inspecting ? null : <>
      <LevelReadout chart="skew" position={linkedPosition("skew")} readings={readings.slice(0, 4)} onLeave={() => { setInspected(null); }} />
      <LevelReadout chart="hodograph" position={linkedPosition("hodograph")} readings={readings.filter((_, index) => index === 0 || index === 1 || index === 4)}
        unplotted={level.hodograph === null} onLeave={() => { setInspected(null); }} />
    </>}
  </div>;
}

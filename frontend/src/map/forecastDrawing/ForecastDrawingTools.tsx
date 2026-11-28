import { Check, ChevronDown, ClipboardPaste, CloudHail, GripHorizontal, Layers, Move, Pentagon, Pencil, Redo2, SwatchBook, Tornado, Trash2, Undo2, Wind, X } from "lucide-react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useShallow } from "zustand/react/shallow";
import { GlassContextBoundary } from "../../components/GlassSurface";
import { Popover } from "../../components/Popover";
import { VisuallyHidden } from "../../components/VisuallyHidden";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { cx } from "../../lib/cx";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useUiStore } from "../../stores/uiStore";
import { FORECAST_CATEGORIES, FORECAST_HATCH_LEVELS, FORECAST_LEVELS, FORECAST_PRODUCTS, FORECAST_PRODUCT_LABELS, MAX_FORECAST_POLYGONS, MAX_FORECAST_VERTICES, type ForecastProduct } from "../../types/forecastDrawing";
import { Action, IconAction, Toggle } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import { ForecastDrawingOutlinePicker, ForecastDrawingOutlineTransfer } from "./ForecastDrawingOutlineTransfer";
import { ForecastDrawingPngControl } from "./ForecastDrawingPngControl";
import { ForecastRiskChoice } from "./ForecastRiskChoice";
import styles from "./ForecastDrawingTools.module.css";

const PRODUCT_BUTTONS = {
  cat: { label: "Category", icon: Layers },
  torn: { label: "Tornado", icon: Tornado },
  hail: { label: "Hail", icon: CloudHail },
  wind: { label: "Wind", icon: Wind },
};
function riskLabel(product: ForecastProduct, level: number, hatch: boolean): string {
  if (hatch) return `CIG ${String(level)}`;
  return product === "cat" ? FORECAST_CATEGORIES[FORECAST_LEVELS.cat.indexOf(level)] ?? String(level) : `${String(level)}%`;
}

export function ForecastDrawingTools({ feedbackTarget }: { feedbackTarget: HTMLElement | null }): ReactNode {
  const { product, level, hatch, mode, editTool, vertexCount, canUndo, canRedo, dragging, selectedId, polygons, result, fixedLevel, saving, error, copiedOutline } = useForecastDrawingStore(useShallow((state) => ({
    product: state.product,
    level: state.level,
    hatch: state.hatch,
    mode: state.mode,
    editTool: state.editTool,
    vertexCount: state.points.length,
    canUndo: state.undoSteps.length > 0,
    canRedo: state.redoSteps.length > 0,
    dragging: state.editGesture !== null,
    selectedId: state.selectedId,
    polygons: state.polygons[state.product],
    result: state.results[state.product],
    fixedLevel: state.editing?.sourceId !== undefined,
    saving: state.saving,
    error: state.error,
    copiedOutline: state.copiedOutline,
  })));
  const outlinesOnly = useUiStore((state) => state.forecastOutlinesOnly);
  const exporting = useForecastStore((state) => state.exporting);
  const backend = useBackendClient();
  const id = "forecast-risk-picker";
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [showAreas, setShowAreas] = useState(false);
  const [applyingOutline, setApplyingOutline] = useState(false);
  const [outlineNotice, setOutlineNotice] = useState<string | null>(null);
  const productButton = useRef<HTMLButtonElement>(null);
  const restoreProductFocus = useRef(false);
  const active = mode !== "idle";
  if (active && applyingOutline) setApplyingOutline(false);
  const copiedName = copiedOutline === null ? null : FORECAST_CATEGORIES[copiedOutline.level] ?? null;
  const transferring = open && !active && applyingOutline && copiedName !== null;
  const moving = editTool === "move";
  const areaActionLabel = active ? "Finish area" : "Draw area";
  const busy = saving || dragging || exporting;
  const hasAreas = (result?.areas.length ?? 0) > 0;
  const areasOpen = showAreas && polygons.length > 0;
  const retryRestore = !active && error !== null && result === undefined && polygons.length > 0;
  const selectedRisk = riskLabel(product, level, hatch);
  const selection = `${FORECAST_PRODUCT_LABELS[product]} · ${selectedRisk}`;
  const hiddenSelection = selectedId !== null && result?.hiddenHatches.includes(selectedId) === true;
  const savedAreaHint = hasAreas ? "Click any risk band to edit it." : undefined;
  const selected = polygons.find((polygon) => polygon.id === selectedId);
  const feedbackTitle = mode === "draw" ? `Draw ${selectedRisk}` : mode === "edit" ? `${moving ? "Move" : "Edit"} ${selectedRisk}`
    : moving ? "Move area" : "Draw forecast";
  const status = saving ? "Updating forecast…"
    : mode === "draw" ? vertexCount < 3 ? `Place vertices · ${String(vertexCount)}/3` : `${String(vertexCount)} vertices · Ready to finish`
    : moving ? (mode === "edit" ? "Drag inside the outline to move it." : "Drag a risk band to move it.")
    : mode === "edit" ? `Drag a vertex or use its arrow keys. ${
      vertexCount < MAX_FORECAST_VERTICES ? "Click an edge handle to add a vertex." : "Vertex limit reached."}`
    : !open && hiddenSelection ? "Hatch hidden. Open Areas to edit it." : null;
  const feedbackNote = status === null ? null : <p className={styles.note} aria-hidden="true">{status}</p>;
  const feedbackError = error === null ? null : <p className={styles.error} role="alert">{error}</p>;

  useLayoutEffect(() => {
    if (!open || transferring || !restoreProductFocus.current) return;
    restoreProductFocus.current = false;
    productButton.current?.focus();
  }, [open, transferring]);

  const useOutline = (): void => {
    setShowAreas(false);
    setOutlineNotice(null);
    setApplyingOutline(true);
  };
  const endOutlineTransfer = (notice: string | null): void => {
    restoreProductFocus.current = true;
    setOutlineNotice(notice);
    setApplyingOutline(false);
  };

  const closePicker = (restoreFocus = true): void => {
    setOpen(false);
    setShowAreas(false);
    setApplyingOutline(false);
    setOutlineNotice(null);
    restoreProductFocus.current = false;
    if (restoreFocus) anchor?.focus();
  };

  const startDrawing = (): void => {
    useForecastDrawingStore.getState().startDraw();
    if (open && useForecastDrawingStore.getState().mode === "draw") closePicker(false);
  };

  const products = <div className={styles.products} role="group" aria-label="Outlook type">
    {FORECAST_PRODUCTS.map((value) => {
      const { icon: Icon, label } = PRODUCT_BUTTONS[value];
      return <Action key={value} ref={product === value ? productButton : null}
        className={cx(styles.choice, styles.product)} aria-pressed={product === value} aria-label={FORECAST_PRODUCT_LABELS[value]}
        data-autofocus={product === value && !active && !busy ? true : undefined} disabled={active || busy}
        onClick={() => {
          if (value === product) return;
          setShowAreas(false);
          setOutlineNotice(null);
          useForecastDrawingStore.getState().setProduct(value);
        }}>
        <Icon className="icon" aria-hidden="true" />{label}
      </Action>;
    })}
  </div>;

  return <div className={styles.tools} role="group" aria-label="Forecast drawing" data-motion="enter">
    <IconAction ref={setAnchor} className={styles.tool} icon={SwatchBook} label="Forecast risk" hintSide="right" hintText={selection}
      aria-description={selection} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={() => { if (open) closePicker(); else setOpen(true); }} />
    <IconAction className={styles.tool} icon={Pentagon} alternateIcon={{ icon: Check, active }}
      label={areaActionLabel} hintSide="right" loading={active && saving}
      hintText={!active && savedAreaHint !== undefined ? `${areaActionLabel} · ${savedAreaHint}` : areaActionLabel}
      aria-description={active ? undefined : savedAreaHint} disabled={busy || (active && vertexCount < 3)}
      onClick={active ? () => { void useForecastDrawingStore.getState().finish(backend); } : startDrawing} />
    {mode !== "draw" && (hasAreas || active) ? <IconAction className={styles.tool} icon={Move} label="Move area"
      hintSide="right" hintText={moving ? "Return to vertex editing" : "Move area"} aria-pressed={moving} disabled={busy}
      onClick={() => { useForecastDrawingStore.getState().setEditTool(moving ? "reshape" : "move"); }} /> : null}
    <VisuallyHidden role="status">{active ? status : outlineNotice ?? status}</VisuallyHidden>
    {feedbackTarget !== null && !open && (status !== null || error !== null) ? createPortal(
      <GlassContextBoundary>
        <Surface as="section" className={styles.feedback} aria-labelledby={`${id}-feedback-title`} data-motion="fade">
          <div className={styles.feedbackCopy}>
            <strong id={`${id}-feedback-title`}>{feedbackTitle}</strong>
            {feedbackNote}
          </div>
          {active ? <div className={styles.sketchActions}>
            <IconAction icon={Undo2} size="sm" label="Undo" hintSide="top" hintText="Undo · Ctrl+Z"
              aria-keyshortcuts="Control+Z Meta+Z" disabled={busy || !canUndo}
              onClick={() => { useForecastDrawingStore.getState().undo(); }} />
            <IconAction icon={Redo2} size="sm" label="Redo" hintSide="top" hintText="Redo · Ctrl+Y"
              aria-keyshortcuts="Control+Y Control+Shift+Z Meta+Y Meta+Shift+Z" disabled={busy || !canRedo}
              onClick={() => { useForecastDrawingStore.getState().redo(); }} />
            <IconAction icon={X} size="sm" label={mode === "edit" ? "Cancel edit" : "Cancel drawing"} disabled={busy}
              onClick={(event) => {
                if (document.activeElement === event.currentTarget) anchor?.focus({ preventScroll: true });
                useForecastDrawingStore.getState().cancel();
              }} />
          </div> : null}
          {feedbackError}
        </Surface>
      </GlassContextBoundary>,
      feedbackTarget,
    ) : null}
    <Popover id={id} open={open} anchor={anchor} onClose={(reason) => { if (reason !== "outside") closePicker(false); }}
      role="dialog" labelledBy={`${id}-title`} initialFocus="first" movable className={styles.picker}>
      <header className={cx(styles.heading, styles.pickerHeader)} data-popover-drag>
        <IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move
          aria-description="Drag the header to move the picker. Use the arrow keys to move or Home to center." />
        <h2 id={`${id}-title`}>Draw forecast</h2>
        <IconAction icon={X} size="sm" label="Close forecast risk" onClick={() => { closePicker(); }} />
      </header>
      <div className={styles.body}>
        {transferring ? <ForecastDrawingOutlineTransfer product={product} outline={copiedName} disabled={busy}
          onCopy={useOutline} onCancel={() => { endOutlineTransfer(null); }}
          onApplied={(probability) => { endOutlineTransfer(`Added ${String(probability)}% area.`); }}>
          {products}
        </ForecastDrawingOutlineTransfer> : <>
          {products}
          {[
            { label: product === "cat" ? "Risk level" : "Probability", levels: FORECAST_LEVELS[product], hatched: false },
            { label: "Conditional intensity", levels: FORECAST_HATCH_LEVELS[product], hatched: true },
          ].map(({ label, levels, hatched }) => {
            if (levels.length === 0) return null;
            const categorical = product === "cat" && !hatched;
            return <div key={label} role="group" aria-label={label} className={styles.risks}>
              <h3>{label}</h3>
              <div className={styles.riskGrid} data-categorical={categorical}>
                {levels.map((value) => {
                  const name = riskLabel(product, value, hatched);
                  const chosen = level === value && hatch === hatched;
                  return <ForecastRiskChoice key={value} product={product} level={value} hatched={hatched} disabled={busy || fixedLevel}
                    aria-label={name} aria-pressed={chosen}
                    onClick={() => { setOutlineNotice(null); useForecastDrawingStore.getState().setRisk(value, hatched); }}>
                    <span>{categorical && value === 0 ? "Thunderstorms" : name}</span>
                  </ForecastRiskChoice>;
                })}
              </div>
            </div>;
          })}
          {!active && product === "cat" ? <ForecastDrawingOutlinePicker disabled={busy} onCopy={useOutline} /> : null}
          {!active && product !== "cat" && copiedName !== null ? <Action className={styles.useOutline} disabled={busy} onClick={useOutline}>
            <ClipboardPaste className="icon-sm" aria-hidden="true" />Use {copiedName} outline
          </Action> : null}
          {!active && outlineNotice !== null ? <p className={styles.note} aria-hidden="true">{outlineNotice}</p> : null}
          {polygons.length > 0 ? <Action className={cx(styles.choice, styles.areaToggle)} aria-expanded={areasOpen}
            aria-controls={`${id}-areas`} onClick={() => { setOutlineNotice(null); setShowAreas(!areasOpen); }}>
            Areas <ChevronDown className="icon-sm" aria-hidden="true" />
          </Action> : null}
          {polygons.length > 0 ? <div className={styles.areaManager} id={`${id}-areas`}
            data-motion="disclosure" hidden={!areasOpen} inert={!areasOpen} aria-hidden={!areasOpen || undefined}>
            <section className={styles.areas} aria-labelledby={`${id}-authored`}>
              <h3 id={`${id}-authored`}>Authored areas</h3>
              <ul className={styles.areaList}>
                {polygons.map((polygon, index) => {
                  const visibility = result?.hiddenHatches.includes(polygon.id) === true ? "Hidden hatch"
                    : result?.clippedHatches.includes(polygon.id) === true ? "Clipped hatch" : null;
                  return <li key={polygon.id}>
                    <Action className={cx(styles.choice, styles.area)} disabled={active || busy} aria-pressed={selectedId === polygon.id}
                      aria-description={visibility ?? undefined} onClick={() => { useForecastDrawingStore.getState().selectPolygon(polygon.id); }}>
                      <span>Area {index + 1} · {riskLabel(product, polygon.level, polygon.hatch)}</span>
                      {visibility === null ? null : <span className={styles.note} aria-hidden="true">{visibility}</span>}
                    </Action>
                  </li>;
                })}
              </ul>
              <div className={styles.choices}>
                <Action className={styles.choice} disabled={active || busy || selectedId === null} onClick={() => {
                  if (selectedId === null) return;
                  const state = useForecastDrawingStore.getState();
                  if (!state.startEdit(selectedId)) return;
                  state.setEditTool("reshape");
                  closePicker();
                }}><Pencil className="icon-sm" aria-hidden="true" />Edit area</Action>
                <Action className={styles.choice} disabled={active || busy || selectedId === null} onClick={() => {
                  if (selectedId === null) return;
                  void useForecastDrawingStore.getState().deletePolygon(selectedId, backend);
                }}><Trash2 className="icon-sm" aria-hidden="true" />{selected?.sourceId === undefined ? "Remove area" : "Reset contour"}</Action>
              </div>
            </section>
            {result !== undefined && result.generatedContours.length > 0 ? <section className={styles.areas} aria-labelledby={`${id}-generated`}>
              <h3 id={`${id}-generated`}>Generated contours</h3>
              <ul className={styles.areaList}>
                {result.generatedContours.map((contour) => {
                  const source = polygons.findIndex((polygon) => polygon.id === contour.sourceId);
                  return <li key={`${contour.sourceId}:${String(contour.level)}`}>
                    <Action className={cx(styles.choice, styles.area)} disabled={active || busy || polygons.length >= MAX_FORECAST_POLYGONS}
                      onClick={() => {
                        if (!useForecastDrawingStore.getState().startEdit(contour)) return;
                        closePicker();
                      }}>
                      <span>{riskLabel(product, contour.level, false)}</span>
                      {polygons.length > 1 && source !== -1 ? <span className={styles.note}>From area {source + 1}</span> : null}
                    </Action>
                  </li>;
                })}
              </ul>
            </section> : null}
          </div> : null}
        </>}
        {open ? <>{feedbackNote}{feedbackError}</> : null}
        {retryRestore ? <Action className={styles.secondary} disabled={busy} onClick={() => {
          void useForecastDrawingStore.getState().restore(backend);
        }}>Retry loading areas</Action> : null}
      </div>
      {transferring ? null : <footer className={styles.footer}>
        <div className={styles.display}>
          <Toggle label="Outlines only" checked={outlinesOnly} disabled={exporting}
            onChange={(value) => { useUiStore.getState().setForecastOutlinesOnly(value); }} />
        </div>
        {!active ? <ForecastDrawingPngControl className={styles.secondary} disabled={busy || !hasAreas}
          unavailableReason={hasAreas ? undefined : "Finish a visible area to export this outlook."} /> : null}
        <Action className={styles.primary} tone="accent" disabled={!active && busy}
          onClick={active ? () => { closePicker(); } : startDrawing}>{active ? "Back to map" : "Draw area"}</Action>
      </footer>}
    </Popover>
  </div>;
}

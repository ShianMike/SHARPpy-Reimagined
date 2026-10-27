import { CalendarDays, ChevronDown, Clock3, CloudSun, SlidersHorizontal } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Surface } from "../../ui/Surface";
import { Action } from "../../ui/Controls";
import { Popover } from "../../components/Popover";
import { cx } from "../../lib/cx";
import { useMapStore } from "../../stores/mapStore";
import { usePanelField } from "../../map/panelField";
import { useFrameDownload } from "../../map/useFrameDownload";
import { useForecast } from "../forecast/useForecast";
import { compactUtc, forecastLabel, formatUtc } from "../sounding/soundingTime";
import { FORECAST_TITLES, ForecastDialog, type ForecastStep } from "./ForecastDialog";
import { ParameterPicker } from "./ParameterPicker";
import styles from "./ForecastControls.module.css";
import hints from "../../ui/HoverHint.module.css";

export const FORECAST_FIELDS = ["Model", "Run", "Forecast hour", "Parameter"] as const;
export const FORECAST_RANGE_ID = "forecast-hour-range";
export interface ForecastControlsProps { className?: string | undefined; }

export function ForecastControls({ className }: ForecastControlsProps): ReactNode {
  const forecast = useForecast();
  const download = useFrameDownload();
  const context = forecast.status === "ready" ? forecast.context : null;
  const panel = useMapStore((state) => state.activePanel);
  const field = usePanelField(panel);
  const [picker, setPicker] = useState<ForecastStep | "parameter">("model");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [parameterAnchor, setParameterAnchor] = useState<HTMLButtonElement | null>(null);
  const [dismissedError, setDismissedError] = useState<Error | null>(null);
  const parameterErrorOpen = field.catalog.isError && field.catalog.error !== dismissedError;
  const reason = forecast.status === "error" ? forecast.error.message : forecast.status === "empty" ? "No models available" : "Loading…";
  const catalogMissing = field.catalog.data === undefined;
  const parameterLabel = catalogMissing ? (field.catalog.isError ? "Unavailable" : "Loading…") : context !== null && !context.fieldsAvailable ? "Soundings only" : field.product?.shortLabel ?? "—";
  const parameterHint = catalogMissing ? (field.catalog.isError ? field.catalog.error.message : "Loading parameters…") : context !== null && !context.fieldsAvailable ? (context.model.soundingsAvailable ? "Map fields unavailable for this model. Point soundings are available." : "Map fields unavailable for this run and hour.") : field.product?.label;
  const open = (kind: ForecastStep | "parameter", element: HTMLButtonElement): void => {
    setAnchor(element); setPicker(kind); setPickerOpen(!(pickerOpen && picker === kind));
  };
  return <Surface className={cx(styles.fields, className)} data-motion="fade" role="group" aria-label="Forecast" data-testid="forecast-fields">
    <button className={cx(styles.trigger, hints.target)} type="button" aria-label="Choose model" aria-description={context?.model.label} aria-haspopup="dialog" aria-expanded={pickerOpen && picker === "model"} disabled={context === null} onClick={(event) => { open("model", event.currentTarget); }}>
      <CloudSun className="icon-sm" aria-hidden="true" /><span className={styles.triggerCopy}><span className={styles.name}>Model</span><strong>{context?.model.label ?? "—"}</strong>{context === null ? <small>{reason}</small> : null}</span><ChevronDown className="icon-sm" aria-hidden="true" />
      <span className={hints.hint} data-side="bottom" aria-hidden="true">{context?.model.label ?? reason}</span>
    </button>
    <button className={cx(styles.trigger, hints.target)} type="button" aria-label="Choose run" aria-description={context === null ? undefined : formatUtc(context.run)} aria-haspopup="dialog" aria-expanded={pickerOpen && picker === "run"} disabled={context === null} onClick={(event) => { open("run", event.currentTarget); }}>
      <CalendarDays className="icon-sm" aria-hidden="true" /><span className={styles.triggerCopy}><span className={styles.name}>Run{context !== null && context.run.slice(0, 4) !== context.model.suggestedRun.slice(0, 4) ? ` · ${context.run.slice(0, 4)}` : ""} · UTC</span><strong>{context === null ? "—" : compactUtc(context.run)}</strong></span><ChevronDown className="icon-sm" aria-hidden="true" />
      <span className={hints.hint} data-side="bottom" aria-hidden="true">{context === null ? reason : formatUtc(context.run)}</span>
    </button>
    <button className={cx(styles.trigger, hints.target)} type="button" aria-label="Choose forecast hour" aria-haspopup="dialog" aria-expanded={pickerOpen && picker === "hour"} aria-controls={FORECAST_RANGE_ID} disabled={context === null} onClick={(event) => { open("hour", event.currentTarget); }}>
      <Clock3 className="icon-sm" aria-hidden="true" /><span className={styles.triggerCopy}><span className={styles.name}>Forecast hour</span><span className={styles.hour}><strong>{context === null ? "—" : forecastLabel(context.hour)}</strong>{context === null ? null : <small>{`of ${forecastLabel(context.hours.at(-1) ?? 0)}`}</small>}</span></span><ChevronDown className="icon-sm" aria-hidden="true" />
      <span className={hints.hint} data-side="bottom" aria-hidden="true">{context === null ? reason : `${forecastLabel(context.hour)} · Valid ${formatUtc(context.validTime)}`}</span>
    </button>
    <button ref={setParameterAnchor} className={cx(styles.trigger, hints.target)} type="button" aria-label="Choose parameter" aria-haspopup="dialog" aria-expanded={field.catalog.isError ? parameterErrorOpen : pickerOpen && picker === "parameter"}
      disabled={context === null || (!field.catalog.isError && (catalogMissing || !context.fieldsAvailable))}
      aria-description={parameterHint}
      onClick={(event) => { if (field.catalog.isError) setDismissedError(null); else open("parameter", event.currentTarget); }}>
      <SlidersHorizontal className="icon-sm" aria-hidden="true" /><span className={styles.triggerCopy}><span className={styles.name}>Parameter{panel > 0 ? ` · ${String(panel + 1)}` : ""}</span><strong>{parameterLabel}</strong></span><ChevronDown className="icon-sm" aria-hidden="true" />
      <span className={hints.hint} data-side="bottom" aria-hidden="true">{parameterHint}</span>
    </button>
    {forecast.status === "error" ? <button type="button" className={styles.retry} onClick={forecast.retry} disabled={forecast.retrying}>Retry models</button> : null}
    <Popover open={parameterErrorOpen} anchor={parameterAnchor} movable role="dialog" label="Parameters unavailable" initialFocus="first"
      className={styles.loadError} onClose={() => { setDismissedError(field.catalog.error); }}>
      <h2>Parameters unavailable</h2>
      {context === null ? null : <p className={styles.errorContext}>{context.model.label} · {compactUtc(context.run)} UTC · {forecastLabel(context.hour)}</p>}
      <p>{field.catalog.error?.message}</p>
      <div className={styles.errorActions}>
        <Action onClick={() => { setDismissedError(field.catalog.error); parameterAnchor?.focus(); }}>Dismiss</Action>
        <Action data-autofocus tone="accent" onClick={() => { void field.catalog.refetch(); }} disabled={field.catalog.isFetching}>{field.catalog.isFetching ? "Retrying…" : "Retry"}</Action>
      </div>
    </Popover>
    <Popover key={picker} id={`forecast-${picker}-picker`} open={pickerOpen && context !== null} movable unframed={picker === "parameter"} anchor={anchor} onClose={() => { setPickerOpen(false); }} role="dialog"
      label={picker === "parameter" ? "Parameters" : FORECAST_TITLES[picker]} initialFocus="first" align={picker === "parameter" ? "end" : "start"} width="content"
      className={picker === "parameter" ? styles.parameterPicker : cx(styles.picker, picker === "hour" && styles.hourPicker, picker === "model" && styles.modelPicker)}>
      {picker !== "parameter" && context !== null ? <ForecastDialog context={context} step={picker} anchor={anchor} download={download} onClose={() => { setPickerOpen(false); }} /> : null}
      {picker === "parameter" && field.catalog.data !== undefined ? <ParameterPicker products={field.catalog.data.products} selected={field.product?.key ?? null} anchor={anchor} onClose={() => { setPickerOpen(false); }} /> : null}
    </Popover>
    <span className={styles.announcement} role="status" aria-label="Forecast downloads">{download.started ? download.message : ""}</span>
  </Surface>;
}

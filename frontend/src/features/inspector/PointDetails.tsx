// The inspector for a forecast point picked on the map (Phase 3): where it is,
// each panel's field value there (read from the frame the panel shows), the
// SPC outlook areas that contain it, and "Open model sounding here", which
// fills the Model form with the forecast time and this point.

import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cx } from "../../lib/cx";
import { convertUnit, displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { drawnRangeNote, formatValue, sampleAt } from "../../map/field/colourize";
import { containsPoint } from "../../map/layers/outlookGeometry";
import { useSpcOutlooks } from "../../map/mapQueries";
import { usePanelField } from "../../map/panelField";
import { useAppStore, type MapPoint } from "../../stores/appStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { OUTLOOK_KEYS, OUTLOOK_NAMES, type OutlookArea, type OutlookKey } from "../../types/map";
import { useForecastContext, type ForecastContext } from "../forecast/useForecast";
import { compactUtc, forecastLabel } from "../sounding/soundingTime";
import styles from "./Inspector.module.css";

function FieldValue({ panel, point, active }: { panel: number; point: MapPoint; active: boolean }): ReactNode {
  const units = useUiStore((state) => state.units);
  const field = usePanelField(panel);
  const frame = field.frame.data;
  if (field.blocked !== null || field.product === null) {
    return null;
  }
  const panelNumber = panel + 1;
  const valueId = `point-value-${String(panelNumber)}`;
  const value = frame === undefined || field.frame.isError ? null : sampleAt(frame, point.longitude, point.latitude);
  const note = value === null ? "" : drawnRangeNote(value, field.product.palette);
  const unit = displayUnit(frame?.units ?? "", units, field.product.palette.display);
  return (
    <li>
      <button type="button" className={styles.reading} aria-label={`Inspect panel ${String(panelNumber)}: ${field.product.label}`} aria-describedby={valueId} aria-pressed={active}
        aria-busy={frame === undefined && !field.frame.isError} onClick={() => { useMapStore.getState().setActivePanel(panel); }} title={`Use panel ${String(panelNumber)}`}>
        <span className={cx(styles.panelNumber, "tabular")} aria-hidden="true">{panelNumber}</span>
        <strong className={styles.fieldName}>{field.product.shortLabel}</strong>
        <span id={valueId} className={cx(styles.readingValue, "tabular")} data-state={value === null ? "unavailable" : "ready"} data-testid={valueId}>
          {field.frame.isError ? "Unavailable" : frame === undefined ? "Loading…" : value === null ? "No data" : formatValue(convertUnit(value, frame.units, unit, field.product.palette.display))}
          {value === null || unit === "" ? null : <span className={styles.units}> {unit}</span>}
        </span>
        <span className={styles.fieldDescription}>{field.product.label}</span>
        {note === "" ? null : <span className={styles.rangeNote}>{note}</span>}
      </button>
    </li>
  );
}

/** The highest-ranked area of each loaded outlook that contains the point. */
function OutlookAreas({ point, validTime }: { point: MapPoint; validTime: string }): ReactNode {
  const flags = useMapStore((state) => {
    const layers = panelLayers(state, state.activePanel);
    return OUTLOOK_KEYS.map((key) => layers[`spc-${key}` as const].visible).join();
  });
  const queries = useSpcOutlooks(flags.split(",").map((flag) => flag === "true"), validTime);
  const found: { key: OutlookKey; area: OutlookArea }[] = [];
  queries.forEach((query, i) => {
    const key = OUTLOOK_KEYS[i];
    const areas = !query.isError && query.data?.outlook?.covers === true ? query.data.outlook.areas : [];
    const hit = [...areas].sort((a, b) => b.rank - a.rank).find((area) => containsPoint(area.rings, [point.longitude, point.latitude]));
    if (key !== undefined && hit !== undefined && flags.split(",")[i] === "true") {
      found.push({ key, area: hit });
    }
  });
  return found.length === 0 ? null : <dl className={styles.outlooks}>{found.map(({ key, area }) => (
    <div className={styles.outlook} key={key}>
      <dt>{OUTLOOK_NAMES[key]}</dt>
      <dd data-testid={`point-outlook-${key}`}>{area.description === "" ? area.label : `${area.label}, ${area.description}`}</dd>
    </div>
  ))}</dl>;
}

/** The Sounding workspace, on its Model form, with the forecast time and this point filled in. */
export function openModelSounding(context: ForecastContext, point: MapPoint): void {
  if (!context.model.soundingsAvailable) return;
  const sounding = useSoundingStore.getState();
  sounding.setSource("model");
  sounding.setModel({
    modelKey: context.model.key,
    runTime: context.run,
    forecastHour: context.model.forecastHours.includes(context.hour) ? context.hour : null,
    latitude: point.latitude.toFixed(4),
    longitude: point.longitude.toFixed(4),
  });
  useAppStore.getState().setActiveWorkspace("sounding");
}

export function PointDetails({ point }: { point: MapPoint }): ReactNode {
  const sourceOpen = useUiStore((state) => state.inspectorSourceOpen);
  const context = useForecastContext();
  const layout = useMapStore((state) => state.panelLayout);
  const activePanel = useMapStore((state) => state.activePanel);
  const anyFieldVisible = useMapStore((state) => Array.from({ length: state.panelLayout }, (_, panel) => panelLayers(state, panel)["model-field"].visible).some(Boolean));
  const field = usePanelField(activePanel);
  const emptyMessage = context === null ? "Loading forecast…" : !context.fieldsAvailable ? "Map fields aren’t available for this model."
    : !anyFieldVisible ? "Turn on a model layer in Layers to read this point."
    : field.catalog.data === undefined ? field.catalog.isError ? "Map parameters unavailable." : "Loading map parameters…" : null;
  return (
    <>
      {emptyMessage !== null ? <p className={styles.readingsEmpty} role="status">{emptyMessage}</p> : <>
        <h3 className={styles.readingsHeading}>Panel readings</h3>
        <ul className={styles.readings} aria-label="Forecast panel readings">
          {Array.from({ length: layout }, (_, panel) => (
            <FieldValue key={panel} panel={panel} point={point} active={activePanel === panel} />
          ))}
        </ul>
      </>}
      {context === null ? null : <OutlookAreas point={point} validTime={context.validTime} />}
      {context === null ? null : <details className={styles.disclosure} open={sourceOpen}
        onToggle={(event) => { useUiStore.getState().setInspectorSourceOpen(event.currentTarget.open); }}>
        <summary>Forecast &amp; source <ChevronDown className="icon icon-sm" aria-hidden="true" /></summary>
        <dl className={styles.metadata}>
          <dt>Model</dt><dd>{context.model.label}</dd>
          <dt>Run (UTC)</dt><dd>{compactUtc(context.run)}</dd>
          <dt>Forecast hour</dt><dd>{forecastLabel(context.hour)}</dd>
          <dt>Valid (UTC)</dt><dd>{compactUtc(context.validTime)}</dd>
          {field.blocked !== null || field.frame.isError || field.frame.data === undefined ? null : <><dt>Source</dt><dd>{field.frame.data.attribution}</dd></>}
        </dl>
      </details>}
    </>
  );
}

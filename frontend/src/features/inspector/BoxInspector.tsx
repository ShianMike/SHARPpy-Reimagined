import { Activity, ChevronDown, MapPinOff, SquareDashedMousePointer } from "lucide-react";
import { type ReactNode } from "react";
import { Action, IconAction } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { boxCentre, boxDimensions } from "../../map/box/boxGeometry";
import { useForecastContext, type ForecastContext } from "../forecast/useForecast";
import type { Bounds } from "../../types/soundings";
import { compactUtc, forecastLabel } from "../sounding/soundingTime";
import { useOpenSounding } from "../sounding/soundingQueries";
import { DownloadStatus } from "../sounding/DownloadStatus";
import { BoxSoundingIssue } from "./BoxSoundingIssue";
import styles from "./Inspector.module.css";

function BoxRequest({ context, bounds, revision }: { context: ForecastContext; bounds: Bounds; revision: number }): ReactNode {
  const choice = useForecastStore.getState();
  const open = useOpenSounding(() => {
    const box = useBoxSoundingStore.getState();
    const current = useForecastStore.getState();
    return useAppStore.getState().activeWorkspace === "map" && box.mode === "box" && !box.editing && box.revision === revision
      && current.modelKey === choice.modelKey && current.runTime === choice.runTime && current.forecastHour === choice.forecastHour;
  });
  const previous = useSoundingStore((state) => state.open.findLast((summary) => summary.provider === context.model.key
    && summary.runTime === context.run && summary.forecastHour === context.hour && summary.box !== undefined
    && summary.box.bounds.west === bounds.west && summary.box.bounds.east === bounds.east
    && summary.box.bounds.south === bounds.south && summary.box.bounds.north === bounds.north));
  const completed = open.data?.box ?? previous?.box;
  const supported = context.model.soundingsAvailable && !context.model.key.startsWith("openmeteo-")
    && context.model.key !== "rdps" && context.model.key !== "gdps";
  const submit = (): void => {
    if (!supported || open.isPending) return;
    const [longitude, latitude] = boxCentre(bounds);
    open.mutate({ kind: "model", model: context.model.key, runTime: context.run, forecastHour: context.hour,
      latitude, longitude, location: null, box: bounds });
  };
  return <>
    <div className={styles.boxMetrics}>
      <span>{context.model.gridSpacingKm === null ? "Native grid" : `${String(context.model.gridSpacingKm)} km grid`}</span>
      {completed === undefined ? null : <span>{completed.validColumns.toLocaleString()} columns
        {completed.validColumns === completed.selectedColumns ? "" : ` / ${completed.selectedColumns.toLocaleString()} selected`}</span>}
    </div>
    {!supported ? <p className={styles.explanation}>Point profiles only. Choose HRRR, GFS or another native-grid model.</p> : null}
    {open.isPending ? <><DownloadStatus label={`${context.model.label} box`} progress={open.progress} />
      <Action onClick={open.cancel}>Cancel</Action></> : null}
    {open.cancelled ? <p role="status" className={styles.explanation}>Box sounding cancelled.</p> : null}
    {open.isError && !open.cancelled ? <BoxSoundingIssue error={open.error} onRetry={submit} busy={open.isPending}
      onRedraw={() => { useBoxSoundingStore.getState().clear(); }} onDismiss={open.reset} /> : null}
    {open.isError && !open.cancelled ? null : <Action tone="accent" disabled={!supported || open.isPending} aria-busy={open.isPending} onClick={submit}>
      <Activity className="icon" aria-hidden="true" />{open.isPending ? "Generating…" : completed === undefined ? "Generate sounding" : "Regenerate sounding"}
    </Action>}
  </>;
}

export function BoxInspector(): ReactNode {
  const bounds = useBoxSoundingStore((state) => state.editing ? state.draft : state.bounds);
  const editing = useBoxSoundingStore((state) => state.editing);
  const revision = useBoxSoundingStore((state) => state.revision);
  const context = useForecastContext();
  const dimensions = bounds === null ? null : boxDimensions(bounds);
  return <Surface as="aside" aria-label="Box sounding" elevation="floating" className={styles.inspector} data-kind="box" data-testid="box-inspector">
    <div className={styles.header}>
      <span className={styles.locationIcon}><SquareDashedMousePointer className="icon" aria-hidden="true" /></span>
      <div className={styles.heading}><p className={styles.id}>Box sounding</p>
        <p className={styles.name}>{dimensions === null ? "Select a region" : `${dimensions[0].toFixed(1)} × ${dimensions[1].toFixed(1)} km`}</p></div>
      <IconAction icon={MapPinOff} size="sm" label="Clear box" disabled={bounds === null} onClick={() => { useBoxSoundingStore.getState().clear(); }} />
    </div>
    {context === null ? null : <div className={styles.context}>
      <strong>{context.model.label} <span className="tabular">{forecastLabel(context.hour)}</span></strong>
      <span className="tabular">Valid {compactUtc(context.validTime)} UTC</span>
    </div>}
    <div className={styles.body}>
      {bounds === null ? null : <p className={styles.boxHint}>Drag corners to resize · Space to pan</p>}
      {bounds === null || editing || context === null ? <p role="status" className={styles.explanation}>
        {editing ? "Adjusting box…" : context === null ? "Loading models…" : "Drag on the map to select a region."}
      </p> : <BoxRequest key={`${context.model.key}:${context.run}:${String(context.hour)}:${String(revision)}`} context={context} bounds={bounds} revision={revision} />}
      <details className={styles.boxHelp}>
        <summary>How it works<ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        <p>Thin traces show up to 64 grid columns. The thick trace and diagnostics use the area mean, above the contributing terrain.</p>
        <p>Drag to draw, drag inside to move, or use the corners to resize. Hold Space to pan. Focus the map and press Enter for a small box; use arrow keys on a corner to resize.</p>
      </details>
    </div>
  </Surface>;
}

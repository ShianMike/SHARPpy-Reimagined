// The inspector body for a station (DESIGN-frontend F8 "Inspector"): only
// real catalogue fields (BRIEF B5): coordinates, availability with its
// explanation, the UWyo source, and which catalogue it came from. Then
// "Zoom to station", which issues a map focus request.

import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cx } from "../../lib/cx";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { useStationAvailability } from "../../map/mapQueries";
import { formatUtc } from "../sounding/soundingTime";
import { Action } from "../../ui/Controls";
import {
  availabilityExplanation,
  availabilityText,
  formatLatitude,
  formatLongitude,
  sourceText,
} from "../../stations/stationFormat";
import type { CatalogOrigin, Station } from "../../types/contracts";
import styles from "./Inspector.module.css";

/** "Bundled UWyo list, 933 stations" or "Seed list, 10 stations". */
export function catalogueText(origin: CatalogOrigin, count: number): string {
  const label = origin === "bundled" ? "Bundled UWyo list" : "Seed list";
  return `${label}, ${String(count)} ${count === 1 ? "station" : "stations"}`;
}

export interface StationDetailsProps {
  station: Station;
  origin: CatalogOrigin;
  stationCount: number;
}

export function StationDetails({ station, origin, stationCount }: StationDetailsProps): ReactNode {
  const sourceOpen = useUiStore((state) => state.inspectorSourceOpen);
  const report = useStationAvailability(useMapStore((state) => state.availabilityTime));
  let status = availabilityText(station.availability);
  let explanation = availabilityExplanation(station.availability);
  if (report.data === undefined) {
    status = "Choose a launch time";
    explanation = "Choose a valid observed launch date and time.";
    if (report.isFetching) {
      status = "Checking…";
      explanation = "Checking the launch with UWyo…";
    } else if (report.isError) {
      status = "Check failed";
      explanation = "Could not check UWyo. Try again.";
    }
  } else if (report.isError) {
    explanation = "Could not refresh UWyo. Showing the last check.";
  }
  return (
    <>
      <dl className={styles.coordinateGrid}>
        <div><dt>Latitude</dt><dd className="tabular">{formatLatitude(station.latitude)}</dd></div>
        <div><dt>Longitude</dt><dd className="tabular">{formatLongitude(station.longitude)}</dd></div>
      </dl>
      <dl className={styles.availability}>
        <dt>Availability</dt>
        <dd role="status" aria-live="polite">
          {status}
          <span className={styles.explanation}>{explanation}</span>
          {report.data === undefined ? null : <span className={cx(styles.explanation, "tabular")}>{`UWyo · ${formatUtc(report.data.observationTime)}`}{report.isFetching ? " · Refreshing…" : ""}</span>}
          {report.isError ? <Action onClick={() => { void report.refetch(); }} disabled={report.isFetching} aria-label="Retry station availability check">Retry check</Action> : null}
        </dd>
      </dl>
      <details className={styles.disclosure} open={sourceOpen}
        onToggle={(event) => { useUiStore.getState().setInspectorSourceOpen(event.currentTarget.open); }}>
        <summary>Station &amp; source <ChevronDown className="icon icon-sm" aria-hidden="true" /></summary>
        <dl className={styles.metadata}>
          <dt>Source</dt>
          <dd>{sourceText(station.source)}</dd>
          <dt>Catalogue</dt>
          <dd className={cx(origin === "seedFallback" ? styles.warning : undefined)}>
            {catalogueText(origin, stationCount)}
          </dd>
        </dl>
      </details>
    </>
  );
}

/** The Sounding workspace, on its Observed form, with this station filled in. */
export function openInSounding(stationId: string): void {
  const sounding = useSoundingStore.getState();
  const time = useMapStore.getState().availabilityTime;
  sounding.setSource("observed");
  sounding.setObserved({ stationId, ...(time === null ? {} : { date: time.slice(0, 10), hour: new Date(time).getUTCHours() }) });
  useAppStore.getState().setActiveWorkspace("sounding");
}

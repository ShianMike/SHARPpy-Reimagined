// The inspector body for a station (DESIGN-frontend F8 "Inspector"): only
// real catalogue fields (BRIEF B5): coordinates, availability with its
// explanation, the UWyo source, and which catalogue it came from. Then
// "Zoom to station", which issues a map focus request.

import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cx } from "../../lib/cx";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
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
  return (
    <>
      <dl className={styles.coordinateGrid}>
        <div><dt>Latitude</dt><dd className="tabular">{formatLatitude(station.latitude)}</dd></div>
        <div><dt>Longitude</dt><dd className="tabular">{formatLongitude(station.longitude)}</dd></div>
      </dl>
      <dl className={styles.availability}>
        <dt>Availability</dt>
        <dd>
          {availabilityText(station.availability)}
          <span className={styles.explanation}>{availabilityExplanation(station.availability)}</span>
        </dd>
      </dl>
      <details className={styles.disclosure}>
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
  sounding.setSource("observed");
  sounding.setObserved({ stationId });
  useAppStore.getState().setActiveWorkspace("sounding");
}

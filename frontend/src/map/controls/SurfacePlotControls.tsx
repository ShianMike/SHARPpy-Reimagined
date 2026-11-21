import type { ReactNode } from "react";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { formatUtc } from "../../features/sounding/soundingTime";
import { displayUnit } from "../../lib/displayUnits";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { useSurfaceLayer } from "../surfaceQueries";
import styles from "./LayerPanel.module.css";

export function SurfacePlotControls(): ReactNode {
  const units = useUiStore((state) => state.units);
  const live = useMapStore((state) => state.view === "live");
  const validTime = useWeatherTime();
  const plots = useSurfaceLayer(live, validTime, true);
  const snapshot = plots.data;
  return (
    <div className={styles.extra}>
      {live && validTime === null ? (
        <p className={styles.hint}>Choose a time to view surface observations.</p>
      ) : plots.isError && (live || snapshot === undefined) ? (
        <SoundingErrorNotice
          error={plots.error}
          operation={live ? "get_surface_plots" : "get_latest_surface_plots"}
          onRetry={() => {
            void plots.refetch();
          }}
          busy={plots.isFetching}
          testId="surface-error"
        />
      ) : snapshot === undefined ? (
        <p className={styles.hint} role="status">Loading surface observations…</p>
      ) : live ? (
        <div className={styles.extra} data-testid="surface-shown">
          <p className={styles.hint}>{`NOAA/NWS · ${formatUtc(snapshot.validTime, { includeYear: false })}`}</p>
          <p className={styles.hint}>{`${String(snapshot.counts.matched)} observations · ${String(snapshot.counts.queried)} of ${String(snapshot.counts.nearby)} stations checked`}</p>
          {snapshot.counts.stale === 0 ? null : <p className={styles.warning}>{`${String(snapshot.counts.stale)} stale observations · shown with a small dashed ring`}</p>}
          {snapshot.counts.noObservation + snapshot.counts.failed === 0 ? null : <p className={styles.hint}>{`${String(snapshot.counts.noObservation)} without a report · ${String(snapshot.counts.failed)} could not be checked`}</p>}
        </div>
      ) : (
        <div className={styles.extra} data-testid="surface-shown">
          <dl className={styles.feedRows}>
            <div><dt>Reporting stations</dt><dd>{snapshot.counts.matched.toLocaleString()}</dd></div>
            <div><dt>Snapshot (UTC)</dt><dd><time dateTime={snapshot.validTime} title={formatUtc(snapshot.validTime)}>
              {formatUtc(snapshot.validTime, { includeYear: false })}
            </time></dd></div>
          </dl>
          <p className={styles.hint}>Latest METAR · NOAA Aviation Weather Center</p>
          {plots.isRefetchError ? <p className={styles.warning} role="status">Refresh failed. Showing the last available snapshot.</p> : null}
        </div>
      )}
      <p className={styles.hint}><span className={styles.obsUpper}>Temperature</span> / <span className={styles.obsLower}>dewpoint</span> in {displayUnit("°C", units)} · wind barbs in kt.</p>
      <p className={styles.hint}>{live ? "Hover for station details."
        : "Hover for a report, click to select a location, or zoom in for station plots."}</p>
    </div>
  );
}

import type { ReactNode } from "react";
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
  const plots = useSurfaceLayer(true);
  const snapshot = plots.data;
  return (
    <div className={styles.extra}>
      {plots.isError && snapshot === undefined ? (
        <SoundingErrorNotice
          error={plots.error}
          operation="get_latest_surface_plots"
          onRetry={() => {
            void plots.refetch();
          }}
          busy={plots.isFetching}
          testId="surface-error"
        />
      ) : snapshot === undefined ? (
        <p className={styles.hint} role="status">Loading surface observations…</p>
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
      <p className={styles.hint}>{live ? "Hover for a report or zoom in for station plots."
        : "Hover for a report, click to select a location, or zoom in for station plots."}</p>
    </div>
  );
}

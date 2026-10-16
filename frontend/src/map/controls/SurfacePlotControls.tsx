// The surface plots' own controls (REQUEST surface observations): the counts
// behind what the layer marks. The provider takes no options; the anchor is
// the map's own selection and the time is the forecast valid time.

import type { ReactNode } from "react";
import { useForecastContext } from "../../features/forecast/useForecast";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { compactUtc } from "../../features/sounding/soundingTime";
import { formatLatitude, formatLongitude } from "../../stations/stationFormat";
import { displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { useSelectionAnchor, useSurfacePlots } from "../surfaceQueries";
import styles from "./LayerPanel.module.css";

export function SurfacePlotControls(): ReactNode {
  const units = useUiStore((state) => state.units);
  const context = useForecastContext();
  const anchor = useSelectionAnchor();
  const plots = useSurfacePlots(anchor, context?.validTime ?? null, true);
  return (
    <div className={styles.extra}>
      {context === null ? (
        <p className={styles.hint}>
          Surface observations follow the forecast valid time, which needs the forecast models.
        </p>
      ) : anchor === null ? (
        <p className={styles.hint}>
          Select a station or a point to see the observations around it.
        </p>
      ) : plots.isError ? (
        <SoundingErrorNotice
          error={plots.error}
          operation="get_surface_plots"
          onRetry={() => {
            void plots.refetch();
          }}
          busy={plots.isFetching}
          testId="surface-error"
        />
      ) : plots.data === undefined ? (
        <p className={styles.hint}>Loading surface observations</p>
      ) : (
        <div className={styles.extra} data-testid="surface-shown">
          <p className={styles.hint}>{`NOAA/NWS · ${compactUtc(plots.data.validTime)} UTC`}</p>
          <p className={styles.hint}>{`Near ${formatLatitude(anchor.latitude)}, ${formatLongitude(anchor.longitude)}`}</p>
          <p className={styles.hint}>{`${String(plots.data.counts.matched)} observations · ${String(plots.data.counts.queried)} of ${String(plots.data.counts.nearby)} stations checked`}</p>
          {plots.data.counts.stale === 0 ? null : <p className={styles.warning}>{`${String(plots.data.counts.stale)} stale observations · shown with a dashed ring`}</p>}
          {plots.data.counts.noObservation + plots.data.counts.failed === 0 ? null : <p className={styles.hint}>{`${String(plots.data.counts.noObservation)} without a report · ${String(plots.data.counts.failed)} could not be checked`}</p>}
        </div>
      )}
      <p className={styles.hint}><span className={styles.obsUpper}>Temperature</span> / <span className={styles.obsLower}>dewpoint</span> in {displayUnit("°C", units)} · wind barbs in kt.</p>
      <p className={styles.hint}>Hover for station details. Zoom in to reveal more observations; click a plot to select its location.</p>
    </div>
  );
}

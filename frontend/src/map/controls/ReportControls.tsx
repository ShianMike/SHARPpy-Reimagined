// The storm reports row's own controls (REQUEST storm-report layer): the event
// window and hazard filter the provider documents, and the counts behind what
// the layer marks.

import type { ReactNode } from "react";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { SourceSelect } from "../../features/sounding/SourceFields";
import { compactUtc } from "../../features/sounding/soundingTime";
import { useMapStore } from "../../stores/mapStore";
import { useReportOptions, useStormReports } from "../mapQueries";
import styles from "./LayerPanel.module.css";

export function ReportControls(): ReactNode {
  const validTime = useWeatherTime();
  const windowS = useMapStore((state) => state.reportWindowS);
  const filter = useMapStore((state) => state.reportFilter);
  const options = useReportOptions(true);
  const reports = useStormReports(validTime, windowS, filter, true);
  return (
    <div className={styles.extra}>
      {options.data === undefined ? null : (
        <>
          <SourceSelect label="Event window" value={windowS}
            options={options.data.windows.map((window) => ({ value: window.seconds, label: window.label }))}
            onChange={useMapStore.getState().setReportWindowS} />
          <SourceSelect label="Hazard" value={filter}
            options={options.data.filters.map((item) => ({ value: item.key, label: item.label }))}
            onChange={useMapStore.getState().setReportFilter} />
        </>
      )}
      {validTime === null ? (
        <p className={styles.hint}>Choose a time to view storm reports.</p>
      ) : reports.isError ? (
        <SoundingErrorNotice
          error={reports.error}
          operation="get_storm_reports"
          onRetry={() => {
            void reports.refetch();
          }}
          busy={reports.isFetching}
          testId="reports-error"
        />
      ) : reports.data === undefined ? (
        <p className={styles.hint}>Loading storm reports</p>
      ) : (
        <p className={styles.hint} data-testid="reports-shown">
          {`${reports.data.shownCount === 0 ? "No matching storm reports" : `${String(reports.data.shownCount)} of ${String(reports.data.totalCount)} reports`}, ${compactUtc(reports.data.validTime)} ${
            reports.data.windowLabel
          }, ${reports.data.hazardFilter}.${reports.data.attribution === "" ? "" : ` ${reports.data.attribution}.`}`}
        </p>
      )}
    </div>
  );
}

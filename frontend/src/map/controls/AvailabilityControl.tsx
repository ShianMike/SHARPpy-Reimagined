// Live station availability (Phase 2): mark the stations against the UWyo
// list for the observed launch or an explicit recent release. While the list
// loads, or if it could not be loaded, the bundled list stays up.

import { useState, type ReactNode } from "react";
import { compactUtc } from "../../features/sounding/soundingTime";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { SourceSelect } from "../../features/sounding/SourceFields";
import { useMapStore } from "../../stores/mapStore";
import { recentReleases } from "../../stations/availability";
import { useStationAvailability } from "../mapQueries";
import styles from "./LayerPanel.module.css";

const RELEASES = 8;
const AUTOMATIC = "automatic";

export function AvailabilityControl(): ReactNode {
  const time = useMapStore((state) => state.availabilityTime);
  const [releases] = useState(() => recentReleases(new Date(), RELEASES));
  const report = useStationAvailability(time);
  const options = time === null || releases.includes(time) ? releases : [time, ...releases];
  return (
    <div className={styles.extra}>
      <SourceSelect label="Availability" value={time ?? AUTOMATIC}
        options={[{ value: AUTOMATIC, label: "Automatic · observed launch" }, ...options.map((item) => ({ value: item, label: `UWyo list for ${compactUtc(item)}` }))]}
        onChange={(value) => { useMapStore.getState().setAvailabilityTime(value === AUTOMATIC ? null : value); }} />
      {report.isError ? (
        <SoundingErrorNotice
          error={report.error}
          operation="get_station_availability"
          onRetry={() => {
            void report.refetch();
          }}
          busy={report.isFetching}
          testId="availability-error"
        />
      ) : report.data === undefined ? (
        <p className={styles.hint} role="status">{report.isFetching ? time === null ? "Checking the observed launch with UWyo…" : `Checking the UWyo list for ${compactUtc(time)}…` : "Choose a valid observed launch time."}</p>
      ) : (
        <p className={styles.hint} data-testid="availability-shown">
          {`UWyo lists ${String(report.data.listedIds.length)} bundled stations and ${String(report.data.extraStations.length)} others for ${compactUtc(report.data.observationTime)}.`}
        </p>
      )}
    </div>
  );
}

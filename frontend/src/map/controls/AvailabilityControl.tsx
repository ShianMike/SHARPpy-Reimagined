// Live station availability (Phase 2): mark the stations against the UWyo
// list for a recent 00Z or 12Z release, or show the bundled list alone. While
// the list loads, or if it could not be loaded, the bundled list stays up.

import { useState, type ReactNode } from "react";
import { compactUtc } from "../../features/sounding/soundingTime";
import { SoundingErrorNotice } from "../../features/sounding/SoundingErrorNotice";
import { SourceSelect } from "../../features/sounding/SourceFields";
import { useMapStore } from "../../stores/mapStore";
import { recentReleases } from "../../stations/availability";
import { useStationAvailability } from "../mapQueries";
import styles from "./LayerPanel.module.css";

const RELEASES = 8;
const BUNDLED = "bundled";

export function AvailabilityControl(): ReactNode {
  const time = useMapStore((state) => state.availabilityTime);
  const [releases] = useState(() => recentReleases(new Date(), RELEASES));
  const report = useStationAvailability(time);
  const options = time === null || releases.includes(time) ? releases : [time, ...releases];
  return (
    <div className={styles.extra}>
      <SourceSelect label="Availability" value={time ?? BUNDLED}
        options={[{ value: BUNDLED, label: "Bundled list only" }, ...options.map((item) => ({ value: item, label: `UWyo list for ${compactUtc(item)}` }))]}
        onChange={(value) => { useMapStore.getState().setAvailabilityTime(value === BUNDLED ? null : value); }} />
      {time === null ? null : report.isError ? (
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
        <p className={styles.hint}>{`Checking the UWyo list for ${compactUtc(time)}`}</p>
      ) : (
        <p className={styles.hint} data-testid="availability-shown">
          {`UWyo lists ${String(report.data.listedIds.length)} bundled stations and ${String(report.data.extraStations.length)} others for ${compactUtc(report.data.observationTime)}.`}
        </p>
      )}
    </div>
  );
}

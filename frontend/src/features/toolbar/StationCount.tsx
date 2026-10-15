// The station count (DESIGN-frontend F8, DR6-3): "933 stations", derived from
// the catalogue query. It renders only while the catalogue has data, and
// nothing while it loads or after it fails, so no count is ever invented.

import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useStationCatalog } from "../../stations/stationQueries";
import styles from "./StationCount.module.css";

export function stationCountText(count: number): string {
  return count === 1 ? "1 station" : `${String(count)} stations`;
}

export interface StationCountProps {
  className?: string | undefined;
}

export function StationCount({ className }: StationCountProps): ReactNode {
  const catalog = useStationCatalog();
  if (catalog.data === undefined) {
    return null;
  }
  return (
    <span className={cx(styles.count, "tabular", className)} data-testid="station-count">
      {stationCountText(catalog.data.stations.length)}
    </span>
  );
}

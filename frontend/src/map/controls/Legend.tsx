// The station legend (DESIGN-frontend F5), inside the layer panel. The listed
// and not-listed swatches appear only when the catalogue carries those states.

import type { ReactNode } from "react";
import type { StationAvailability } from "../../types/contracts";
import styles from "./Legend.module.css";

export interface LegendProps {
  availabilities: ReadonlySet<StationAvailability>;
}

const ENTRIES: readonly { key: string; label: string; swatch: string; shown: (set: ReadonlySet<StationAvailability>) => boolean }[] = [
  { key: "unknown", label: "Station, availability unknown", swatch: "unknown", shown: () => true },
  { key: "listed", label: "Station, listed", swatch: "listed", shown: (set) => set.has("listed") },
  { key: "notListed", label: "Station, not listed", swatch: "notListed", shown: (set) => set.has("notListed") },
  { key: "selected", label: "Selected station", swatch: "selected", shown: () => true },
];

export function Legend({ availabilities }: LegendProps): ReactNode {
  return (
    <ul className={styles.legend} aria-label="Legend">
      {ENTRIES.filter((entry) => entry.shown(availabilities)).map((entry) => (
        <li key={entry.key} className={styles.entry}>
          <span className={styles.swatch} data-swatch={entry.swatch} aria-hidden="true" />
          {entry.label}
        </li>
      ))}
    </ul>
  );
}

// The location chip (DESIGN-frontend F8): "No location selected", or the id
// and display name of the selected station, with a clear button. The text
// truncates with an ellipsis between --chip-min-w and --chip-max-w; the full
// text stays in the DOM and in title, so the accessible name is complete.

import { MapPin, X } from "lucide-react";
import type { ReactNode } from "react";
import { IconAction } from "../../ui/Controls";
import { cx } from "../../lib/cx";
import { useAppStore } from "../../stores/appStore";
import { formatCoordinates, stationTitle } from "../../stations/stationFormat";
import { useSelectedStation } from "../../stations/stationIndex";
import styles from "./LocationChip.module.css";

export const NO_LOCATION = "No location selected";

export interface LocationChipProps {
  className?: string | undefined;
}

export function LocationChip({ className }: LocationChipProps): ReactNode {
  const selection = useSelectedStation();
  const point = useAppStore((state) => state.selectedPoint);
  let text: string;
  if (point !== null) {
    text = `Point ${formatCoordinates(point)}`;
  } else {
    text =
      selection.state === "none" ? NO_LOCATION : selection.state === "found" ? stationTitle(selection.station) : selection.id;
  }
  const empty = point === null && selection.state === "none";
  return (
    <div className={cx(styles.chip, className)} role="group" aria-label="Location" data-testid="location-chip">
      <MapPin className="icon" aria-hidden="true" />
      <span className={styles.text} data-empty={empty} title={text}>
        {text}
      </span>
      {empty ? null : (
        <IconAction
          icon={X}
          label="Clear location"
          size="sm"
          className={styles.clear}
          onClick={() => {
            useAppStore.getState().clearSelection();
          }}
        />
      )}
    </div>
  );
}

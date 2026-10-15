// The station tooltip (DESIGN-frontend F5): a glass popover near the pointer,
// clamped inside the map, role="tooltip", with no pointer events. It reads the
// hover store only; the runtime clears it while the map is dragged. React
// escapes every string, so UWyo free text cannot inject markup.

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { Surface } from "../../ui/Surface";
import { cx } from "../../lib/cx";
import { useStationCatalog } from "../../stations/stationQueries";
import { stationIndex } from "../../stations/stationIndex";
import {
  availabilityText,
  displayName,
  formatCoordinates,
  sourceText,
} from "../../stations/stationFormat";
import type { StationAvailability } from "../../types/contracts";
import { useHoverStore } from "./hoverStore";
import styles from "./StationTooltip.module.css";

function availabilityLine(availability: StationAvailability): string {
  return availability === "unknown" ? "Availability unknown" : `Availability: ${availabilityText(availability)}`;
}

export function StationTooltip(): ReactNode {
  const hovered = useHoverStore((state) => state.hovered);
  const catalog = useStationCatalog();
  const ref = useRef<HTMLElement | null>(null);
  const station =
    hovered === null || catalog.data === undefined
      ? undefined
      : stationIndex(catalog.data.stations).get(hovered.stationId);

  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null || hovered === null) {
      return;
    }
    const box = element.getBoundingClientRect();
    element.style.setProperty("--tooltip-x", `${String(hovered.x)}px`);
    element.style.setProperty("--tooltip-y", `${String(hovered.y)}px`);
    element.style.setProperty("--tooltip-w", `${String(box.width)}px`);
    element.style.setProperty("--tooltip-h", `${String(box.height)}px`);
  });

  if (hovered === null || station === undefined) {
    return null;
  }
  return (
    <Surface ref={ref} elevation="popover" role="tooltip" className={styles.tooltip} data-testid="station-tooltip">
      <p className={cx(styles.id, "tabular")}>{station.id}</p>
      <p className={styles.name}>{displayName(station.name)}</p>
      <p className={cx(styles.meta, "tabular")}>{formatCoordinates(station)}</p>
      <p className={styles.meta}>{availabilityLine(station.availability)}</p>
      <p className={styles.meta}>{sourceText(station.source)}</p>
    </Surface>
  );
}

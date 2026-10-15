// The SPC outlook tooltip (REQUEST §8 hover): the area under the pointer, its
// SPC description, and the outlook it belongs to. Positioned like the station
// tooltip, which wins when both apply.

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { Surface } from "../../ui/Surface";
import { useHoverStore } from "./hoverStore";
import styles from "./StationTooltip.module.css";

export function OutlookTooltip(): ReactNode {
  const outlook = useHoverStore((state) => state.outlook);
  const ref = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null || outlook === null) {
      return;
    }
    const box = element.getBoundingClientRect();
    element.style.setProperty("--tooltip-x", `${String(outlook.x)}px`);
    element.style.setProperty("--tooltip-y", `${String(outlook.y)}px`);
    element.style.setProperty("--tooltip-w", `${String(box.width)}px`);
    element.style.setProperty("--tooltip-h", `${String(box.height)}px`);
  });

  if (outlook === null) {
    return null;
  }
  return (
    <Surface ref={ref} elevation="popover" role="tooltip" className={styles.tooltip} data-testid="outlook-tooltip">
      <p className={styles.id}>{outlook.label}</p>
      {outlook.description === "" ? null : <p className={styles.name}>{outlook.description}</p>}
      <p className={styles.meta}>{outlook.title}</p>
      {outlook.subtitle === "" ? null : <p className={styles.meta}>{outlook.subtitle}</p>}
    </Surface>
  );
}

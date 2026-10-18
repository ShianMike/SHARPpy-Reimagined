// Provider area readout for SPC outlooks and NWS warnings/watches. Positioned
// like the station tooltip, which wins when both apply.

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
  const risk = outlook.description || outlook.label;
  return (
    <Surface ref={ref} elevation="popover" role="tooltip" className={[styles.tooltip, styles.report].join(" ")} data-testid="outlook-tooltip">
      <header className={styles.reportHeading}>
        <p className={styles.reportTitle}>{risk.includes(outlook.label) ? risk : `${risk} (${outlook.label})`}</p>
        <p className={styles.meta}>{outlook.title}</p>
      </header>
      {outlook.subtitle === "" ? null : <footer className={styles.reportAttribution}>
        <p>{outlook.subtitle}</p>
      </footer>}
    </Surface>
  );
}

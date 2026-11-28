// Provider area readout for SPC outlooks and NWS warnings/watches. Positioned
// like the station tooltip, which wins when both apply.

import { useRef, type CSSProperties, type ReactNode } from "react";
import { Surface } from "../../ui/Surface";
import { useTooltipSize } from "./FieldTooltip";
import { useHoverStore } from "./hoverStore";
import styles from "./StationTooltip.module.css";

export function OutlookTooltip(): ReactNode {
  const outlook = useHoverStore((state) => state.outlook);
  const ref = useRef<HTMLElement | null>(null);
  useTooltipSize(ref, outlook === null ? null : `${outlook.label}\u0000${outlook.title}`);

  if (outlook === null) {
    return null;
  }
  const risk = outlook.description || outlook.label;
  return (
    <Surface ref={ref} elevation="popover" role="tooltip" className={[styles.tooltip, styles.report].join(" ")} data-testid="outlook-tooltip"
      style={{ "--tooltip-x": `${String(outlook.x)}px`, "--tooltip-y": `${String(outlook.y)}px` } as CSSProperties}>
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

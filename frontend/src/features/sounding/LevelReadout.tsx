import { useLayoutEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { GlassContextBoundary } from "../../components/GlassSurface";
import { Surface } from "../../ui/Surface";
import { cx } from "../../lib/cx";
import tooltip from "../../map/hover/StationTooltip.module.css";
import styles from "./Viewer.module.css";

export function LevelReadout({ position, readings, chart, unplotted = false, onLeave }: {
  position: { x: number; y: number }; readings: [string, string][]; chart: "skew" | "hodograph"; unplotted?: boolean; onLeave: () => void;
}): ReactNode {
  const ref = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    const box = element.getBoundingClientRect();
    element.style.setProperty("--tooltip-w", `${String(box.width)}px`);
    element.style.setProperty("--tooltip-h", `${String(box.height)}px`);
  });
  return createPortal(<GlassContextBoundary>
    <Surface ref={ref} elevation="popover" role="status" aria-label={chart === "skew" ? "Skew-T readout" : "Hodograph readout"} aria-atomic="true"
      className={cx(tooltip.tooltip, styles.levelReadout)} data-sounding-level="true" onPointerLeave={onLeave}
      style={{ "--tooltip-x": `${String(position.x)}px`, "--tooltip-y": `${String(position.y)}px` } as CSSProperties}>
      <strong>{chart === "skew" ? "Skew-T" : "Hodograph"}</strong>
      <dl>{readings.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      {unplotted ? <small>No hodograph point is plotted at this level.</small> : null}
    </Surface>
  </GlassContextBoundary>, document.body);
}

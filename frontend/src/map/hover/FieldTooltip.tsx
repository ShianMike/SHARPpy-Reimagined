import { useLayoutEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { convertUnit, displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { Surface } from "../../ui/Surface";
import { drawnRangeNote, formatValue, sampleAt } from "../field/colourize";
import type { PanelField } from "../panelField";
import { useHoverStore } from "./hoverStore";
import styles from "./StationTooltip.module.css";

/** Reads one cached raw cell; pointer movement never updates the map scene. */
export function FieldTooltip({ panel, field }: { panel: number; field: PanelField }): ReactNode {
  const units = useUiStore((state) => state.units);
  const pointer = useHoverStore((state) => state.pointer?.panel === panel ? state.pointer : null);
  const ref = useRef<HTMLElement | null>(null);
  const frame = field.frame.data;
  const palette = field.product?.palette;
  const visible = pointer !== null && !field.frame.isError && frame !== undefined && palette !== undefined && field.blocked === null;
  const value = visible ? sampleAt(frame, pointer.longitude, pointer.latitude) : null;
  const note = value === null || palette === undefined ? "" : drawnRangeNote(value, palette);
  const source = frame?.units ?? "";
  const unit = displayUnit(source, units, palette?.display);
  const text = value === null ? "No data here" : `${formatValue(convertUnit(value, source, unit, palette?.display))} ${unit}${note === "" ? "" : ` (${note})`}`;
  const label = field.product?.label;
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    const box = element.getBoundingClientRect();
    element.style.setProperty("--tooltip-w", `${String(box.width)}px`);
    element.style.setProperty("--tooltip-h", `${String(box.height)}px`);
  }, [visible, text, label]);
  if (!visible) return null;
  return <Surface ref={ref} elevation="popover" role="tooltip" className={cx(styles.tooltip, styles.field)} data-testid="pointer-value"
    style={{ "--tooltip-x": `${String(pointer.x)}px`, "--tooltip-y": `${String(pointer.y)}px` } as CSSProperties}>
    <span className={styles.meta}>{label}</span><strong className="tabular">{text}</strong>
  </Surface>;
}

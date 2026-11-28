import { useLayoutEffect, useRef, type CSSProperties, type ReactNode, type RefObject } from "react";
import { cx } from "../../lib/cx";
import { convertUnit, displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { Surface } from "../../ui/Surface";
import { decodePtypeValue, drawnRangeNote, formatValue, sampleAt } from "../field/colourize";
import type { PanelField } from "../panelField";
import { useHoverStore, type PointerPosition } from "./hoverStore";
import styles from "./StationTooltip.module.css";

const coord = (value: number): string => (Number(value.toFixed(4)) || 0).toFixed(4);

/** Writes --tooltip-w/-h when the box changes, never on every pointer move. */
export function useTooltipSize(ref: RefObject<HTMLElement | null>, key: unknown): void {
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    let width = -1;
    let height = -1;
    const measure = (): void => {
      const box = element.getBoundingClientRect();
      if (box.width === width && box.height === height) return;
      width = box.width;
      height = box.height;
      element.style.setProperty("--tooltip-w", `${String(width)}px`);
      element.style.setProperty("--tooltip-h", `${String(height)}px`);
    };
    measure();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    observer?.observe(element);
    return () => { observer?.disconnect(); };
  }, [ref, key]);
}

/** Reads one cached raw cell; pointer movement never updates the map scene. */
export function FieldTooltip({ panel, field }: { panel: number; field: PanelField }): ReactNode {
  const units = useUiStore((state) => state.units);
  const pointer = useHoverStore((state) => state.pointer?.panel === panel ? state.pointer : null);
  const frame = field.frame.data;
  const palette = field.product?.palette;
  const visible = pointer !== null && !field.frame.isError && frame !== undefined && palette !== undefined && field.blocked === null;
  const value = visible ? sampleAt(frame, pointer.longitude, pointer.latitude) : null;
  const decoded = value === null || palette === undefined ? null : decodePtypeValue(value, palette);
  const note = decoded === null || palette === undefined ? "" : drawnRangeNote(decoded.amount, palette);
  const source = frame?.units ?? "";
  const unit = displayUnit(source, units, palette?.display);
  const suffix = [decoded?.ptypeLabel, note === "" ? null : note].filter((part): part is string => part != null && part !== "").join(", ");
  const text = decoded === null ? "No data here" : `${formatValue(convertUnit(decoded.amount, source, unit, palette?.display))} ${unit}${suffix === "" ? "" : ` (${suffix})`}`;
  const label = pointer === null ? undefined : `${coord(pointer.latitude)}, ${coord(pointer.longitude)}`;
  return <PointerValue pointer={visible ? pointer : null} label={label} text={text} />;
}

export function PointerValue({ pointer, label, text }: { pointer: PointerPosition | null; label: string | undefined; text: string }): ReactNode {
  const ref = useRef<HTMLElement | null>(null);
  const visible = pointer !== null;
  useTooltipSize(ref, visible);
  if (!visible) return null;
  return <Surface ref={ref} elevation="popover" role="tooltip" className={cx(styles.tooltip, styles.field)} data-testid="pointer-value"
    style={{ "--tooltip-x": `${String(pointer.x)}px`, "--tooltip-y": `${String(pointer.y)}px` } as CSSProperties}>
    <span className={cx(styles.meta, "tabular")}>{label}</span><strong className="tabular">{text}</strong>
  </Surface>;
}

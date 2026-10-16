import { X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Action, IconAction } from "../../ui/Controls";
import type { ViewPoint } from "../../types/soundingView";
import { convertUnit, displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import styles from "./Viewer.module.css";

export function PointEditor({ point, busy, onApply, onClose }: {
  point: ViewPoint; busy: boolean; onApply: (values: Record<string, number>) => void; onClose: () => void;
}): ReactNode {
  const units = useUiStore((state) => state.units);
  const fields = point.values.map((field) => {
    const unit = displayUnit(field.unit, units);
    return { ...field, displayUnit: unit, text: String(Number(convertUnit(field.value, field.unit, unit).toFixed(2))) };
  });
  const [values, setValues] = useState(Object.fromEntries(fields.map((field) => [field.key, field.text])));
  const applied = Object.fromEntries(fields.map((field) => [field.key, values[field.key] === field.text
    ? field.value : convertUnit(Number(values[field.key]), field.displayUnit, field.unit)]));
  const valid = point.values.every((field) => {
    const value = applied[field.key] ?? NaN;
    return values[field.key]?.trim() !== "" && Number.isFinite(value) && value >= field.min && value <= field.max;
  });
  return <form className={styles.editor} data-motion="enter" aria-label="Edit selected level" onSubmit={(event) => {
    event.preventDefault(); if (valid && !busy) onApply(applied);
  }}>
    <header><strong>{point.label}</strong><IconAction icon={X} label="Close level editor" size="sm" onClick={onClose} /></header>
    <div className={styles.editFields}>{fields.map((field) => <label key={field.key}>{field.label}<span>
      <input type="number" aria-label={`${field.label} ${field.displayUnit}`} step="any" min={Math.floor(convertUnit(field.min, field.unit, field.displayUnit) * 100) / 100} max={Math.ceil(convertUnit(field.max, field.unit, field.displayUnit) * 100) / 100} value={values[field.key] ?? ""} disabled={busy}
        onChange={(event) => { setValues({ ...values, [field.key]: event.target.value }); }} />{field.displayUnit}
    </span></label>)}</div>
    <Action type="submit" tone="accent" disabled={!valid || busy}>Apply & recalculate</Action>
    <small>The original sounding is kept. Undo any edit.</small>
  </form>;
}

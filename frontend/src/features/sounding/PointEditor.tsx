import { X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Action, IconAction } from "../../ui/Controls";
import type { ViewPoint } from "../../types/soundingView";
import styles from "./Viewer.module.css";

export function PointEditor({ point, busy, onApply, onClose }: {
  point: ViewPoint; busy: boolean; onApply: (values: Record<string, number>) => void; onClose: () => void;
}): ReactNode {
  const [values, setValues] = useState(Object.fromEntries(point.values.map((field) => [field.key, String(Number(field.value.toFixed(2)))])));
  const valid = point.values.every((field) => {
    const value = Number(values[field.key]);
    return values[field.key]?.trim() !== "" && Number.isFinite(value) && value >= field.min && value <= field.max;
  });
  return <form className={styles.editor} data-motion="enter" aria-label="Edit selected level" onSubmit={(event) => {
    event.preventDefault(); if (valid && !busy) onApply(Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)])));
  }}>
    <header><strong>{point.label}</strong><IconAction icon={X} label="Close level editor" size="sm" onClick={onClose} /></header>
    <div className={styles.editFields}>{point.values.map((field) => <label key={field.key}>{field.label}<span>
      <input type="number" step="any" min={field.min} max={field.max} value={values[field.key] ?? ""} disabled={busy}
        onChange={(event) => { setValues({ ...values, [field.key]: event.target.value }); }} />{field.unit}
    </span></label>)}</div>
    <Action type="submit" tone="accent" disabled={!valid || busy}>Apply & recalculate</Action>
    <small>The original sounding is kept. Undo any edit.</small>
  </form>;
}

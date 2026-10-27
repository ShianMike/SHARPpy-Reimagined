import { LoaderCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Action } from "../../ui/Controls";
import type { ViewPoint } from "../../types/soundingView";
import { convertUnit, displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { useUpdateStore } from "../../stores/updateStore";
import styles from "./Viewer.module.css";

export function PointEditor({ point, busy, onApply }: {
  point: ViewPoint; busy: boolean; onApply: (values: Record<string, number>) => void;
}): ReactNode {
  const units = useUiStore((state) => state.units);
  const fields = point.values.map((field) => {
    const unit = displayUnit(field.unit, units);
    return { ...field, displayUnit: unit, text: String(Number(convertUnit(field.value, field.unit, unit).toFixed(2))),
      before: field.original === null ? "Unavailable" : `${String(Number(convertUnit(field.original, field.unit, unit).toFixed(2)))} ${unit}` };
  });
  const [values, setValues] = useState(Object.fromEntries(fields.map((field) => [field.key, field.text])));
  const applied = Object.fromEntries(fields.map((field) => [field.key, values[field.key] === field.text
    ? field.value : convertUnit(Number(values[field.key]), field.displayUnit, field.unit)]));
  const valid = point.values.every((field) => {
    const value = applied[field.key] ?? NaN;
    return values[field.key]?.trim() !== "" && Number.isFinite(value) && value >= field.min && value <= field.max;
  });
  return <form className={styles.editor} aria-label="Edit selected level" aria-busy={busy} onSubmit={(event) => {
    event.preventDefault(); if (valid && !busy) onApply(applied);
  }}>
    <div className={styles.comparisonLabels} aria-hidden="true"><span>Original</span><span>New</span></div>
    <div className={styles.editFields}>{fields.map((field) => <label key={field.key} data-edited={field.original !== null && applied[field.key] !== field.original}>
      {fields.length > 1 ? <strong>{field.label}</strong> : null}
      <div className={styles.valueComparison}><output aria-label={`${field.label} before`}>{field.before}</output><span className={styles.afterValue}>
      <input type="number" aria-label={`${field.label} ${field.displayUnit}`} step="any" min={Math.floor(convertUnit(field.min, field.unit, field.displayUnit) * 100) / 100} max={Math.ceil(convertUnit(field.max, field.unit, field.displayUnit) * 100) / 100} value={values[field.key] ?? ""} disabled={busy}
        onChange={(event) => { setValues({ ...values, [field.key]: event.target.value }); useUpdateStore.getState().noteSoundingWork(); }} /><span aria-hidden="true">{field.displayUnit}</span>
    </span></div></label>)}</div>
    <Action type="submit" tone="accent" disabled={!valid || busy} aria-label="Apply changes" aria-description="Recalculates the profile. Use Undo to revert.">{busy ? <LoaderCircle className="icon-sm" data-loading="true" aria-hidden="true" /> : null}{busy ? "Applying…" : "Apply"}</Action>
  </form>;
}

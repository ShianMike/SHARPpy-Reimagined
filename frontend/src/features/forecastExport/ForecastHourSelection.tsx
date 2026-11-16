import { useId, useRef, type ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { forecastLabel } from "../sounding/soundingTime";
import styles from "./ForecastExportDialog.module.css";

export function ForecastHourSelection({ hours, currentHour, selected, disabled, onChange }: {
  hours: readonly number[]; currentHour: number; selected: readonly number[]; disabled: boolean;
  onChange: (hours: number[]) => void;
}): ReactNode {
  const hint = useId();
  const anchor = useRef<number | null>(null);
  const shift = useRef(false);
  const selection = hours.filter(hour => selected.includes(hour));
  const choose = (next: number[]): void => { anchor.current = null; onChange(next); };
  return <fieldset disabled={disabled} className={styles.hoursSection}>
    <legend>Forecast hours <span>{selection.length} selected</span></legend>
    <div className={styles.hourActions}>
      <Action onClick={() => { choose([...hours]); }}>Select all</Action>
      <Action onClick={() => { choose([]); }}>Clear</Action>
      <Action onClick={() => { choose(hours.filter(hour => hour >= currentHour && hour <= currentHour + 6)); }}
        title="Include the current hour through six hours ahead">Next 6 hours</Action>
      <Action onClick={() => { choose(hours.filter(hour => hour >= currentHour && hour <= currentHour + 12)); }}
        title="Include the current hour through twelve hours ahead">Next 12 hours</Action>
      <Action disabled={selection.length < 2} onClick={() => { const first = selection[0];
        if (first !== undefined) choose(selection.filter(hour => (hour - first) % 3 === 0));
      }} title="Keep hours three hours apart, starting with the first selected hour">Every 3 hours</Action>
    </div>
    <p id={hint} className={styles.hourHint}>Ranges start at {forecastLabel(currentHour)} and include the endpoint. Shift-click to select or clear a range.</p>
    <div className={styles.hours} role="group" aria-label="Hours to export" aria-describedby={hint}>{hours.map(hour => <label key={hour}>
      <input type="checkbox" aria-label={`Export forecast hour ${String(hour)}`} checked={selected.includes(hour)}
        onClick={event => { shift.current = event.shiftKey; }} onChange={event => {
          if (!shift.current || anchor.current === null || !hours.includes(anchor.current)) anchor.current = hour;
          const start = anchor.current;
          const next = new Set(selected);
          for (const value of hours) if (value >= Math.min(start, hour) && value <= Math.max(start, hour)) {
            if (event.currentTarget.checked) next.add(value); else next.delete(value);
          }
          shift.current = false;
          onChange(hours.filter(value => next.has(value)));
        }} /><span>{forecastLabel(hour)}</span>
    </label>)}</div>
  </fieldset>;
}

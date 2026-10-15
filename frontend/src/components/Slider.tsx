// A native range input with its label and a visible value (DESIGN-frontend F8).

import { useId, type ReactNode } from "react";
import { cx } from "../lib/cx";
import styles from "./Slider.module.css";

export interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  /** Text for the visible value and aria-valuetext, e.g. "80%". */
  formatValue: (value: number) => string;
  disabled?: boolean;
  /** Shorter visible text when the row already names the subject; label stays the accessible name. */
  visibleLabel?: string;
}

export function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  formatValue,
  disabled = false,
  visibleLabel,
}: SliderProps): ReactNode {
  const id = useId();
  const text = formatValue(value);
  return (
    <div className={styles.slider}>
      <label className={styles.label} htmlFor={id}>
        {visibleLabel ?? label}
      </label>
      <input
        id={id}
        aria-label={visibleLabel === undefined ? undefined : label}
        className={styles.input}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        aria-valuetext={text}
        onChange={(event) => {
          onChange(Number(event.currentTarget.value));
        }}
      />
      <output className={cx(styles.value, "tabular")} htmlFor={id}>
        {text}
      </output>
    </div>
  );
}

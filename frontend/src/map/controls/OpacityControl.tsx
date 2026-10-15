// A layer's opacity slider, 0 to 100 percent in 5 percent steps.

import type { ReactNode } from "react";
import { RangeControl } from "../../ui/Controls";

export interface OpacityControlProps {
  label: string;
  /** 0 to 1. */
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}

const percent = (value: number): string => `${String(Math.round(value * 100))}%`;

export function OpacityControl({ label, value, onChange, disabled = false }: OpacityControlProps): ReactNode {
  return (
    <RangeControl
      label={label}
      value={value}
      min={0}
      max={1}
      step={0.05}
      onChange={onChange}
      formatValue={percent}
      disabled={disabled}
      visibleLabel="Opacity"
    />
  );
}

import { useId, type ReactNode } from "react";
import { FORECAST_OUTLOOK_DAYS, MAX_FORECAST_AUTHOR_LENGTH, type ForecastDrawingDetails } from "../../types/forecastDrawing";
import { SourceSelect } from "../sounding/SourceFields";
import fields from "../sounding/Sounding.module.css";
import styles from "./ForecastDrawingDetailsFields.module.css";

const DAY_OPTIONS = [
  { value: 0 as const, label: "Unspecified" },
  ...FORECAST_OUTLOOK_DAYS.map((value) => ({ value, label: `Day ${String(value)}` })),
];

export function ForecastDrawingDetailsFields({ value, disabled, onChange }: {
  value: ForecastDrawingDetails;
  disabled: boolean;
  onChange: (value: ForecastDrawingDetails) => void;
}): ReactNode {
  const authorId = useId();
  return <div className={styles.fields}>
    <div className={fields.field}>
      <label htmlFor={authorId}>Author (optional)</label>
      <input id={authorId} className={fields.input} type="text" data-autofocus autoComplete="off" maxLength={MAX_FORECAST_AUTHOR_LENGTH}
        placeholder="Name or team" value={value.author} disabled={disabled}
        onChange={(event) => { onChange({ ...value, author: event.currentTarget.value }); }} />
    </div>
    <SourceSelect label="Outlook day" value={value.day ?? 0} options={DAY_OPTIONS} disabled={disabled}
      onChange={(day) => { onChange({ ...value, day: day === 0 ? null : day }); }} />
  </div>;
}

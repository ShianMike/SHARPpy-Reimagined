import { useLayoutEffect, useRef, type ReactNode } from "react";
import { useShallow } from "zustand/react/shallow";
import { SourceSelect } from "../../features/sounding/SourceFields";
import { useBackendClient } from "../../lib/backend/BackendProvider";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { FORECAST_CATEGORIES, FORECAST_LEVELS, type ForecastProduct, type GeneratedForecastContour } from "../../types/forecastDrawing";
import { Action } from "../../ui/Controls";
import { ForecastRiskChoice } from "./ForecastRiskChoice";
import styles from "./ForecastDrawingTools.module.css";

interface OutlineChoice {
  value: string;
  label: string;
  level: number;
  target: string | GeneratedForecastContour;
}

export function ForecastDrawingOutlinePicker({ disabled, label = "Copy outline", onCopy }: {
  disabled: boolean;
  label?: string;
  onCopy: () => void;
}): ReactNode {
  const { polygons, generated } = useForecastDrawingStore(useShallow((state) => ({
    polygons: state.polygons.cat,
    generated: state.results.cat?.generatedContours,
  })));
  const choices: OutlineChoice[] = [];
  for (const [index, polygon] of polygons.entries()) {
    const suffix = polygons.length > 1 ? " · Area " + String(index + 1) : "";
    if (polygon.level > 0 && !polygon.hatch) {
      choices.push({ value: "authored-" + polygon.id, label: String(FORECAST_CATEGORIES[polygon.level]) + suffix,
        level: polygon.level, target: polygon.id });
    }
    for (const contour of generated ?? []) {
      if (contour.sourceId !== polygon.id || contour.level === 0) continue;
      choices.push({ value: "generated-" + polygon.id + "-" + String(contour.level), label: String(FORECAST_CATEGORIES[contour.level]) + suffix,
        level: contour.level, target: contour });
    }
  }
  if (choices.length === 0) return null;
  choices.sort((a, b) => b.level - a.level);
  return <SourceSelect label={label} placeholder={label + "…"} compact value="" options={choices} disabled={disabled}
    onChange={(value) => {
      const target = choices.find((choice) => choice.value === value)?.target;
      if (target !== undefined && useForecastDrawingStore.getState().copyCategoricalOutline(target)) onCopy();
    }} />;
}

export function ForecastDrawingOutlineTransfer({ product, outline, disabled, onCopy, onCancel, onApplied, children }: {
  product: ForecastProduct;
  outline: string;
  disabled: boolean;
  onCopy: () => void;
  onCancel: () => void;
  onApplied: (probability: number) => void;
  children: ReactNode;
}): ReactNode {
  const backend = useBackendClient();
  const heading = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => { heading.current?.focus(); }, []);

  return <>
    <div className={styles.transferHeader}>
      <h3 ref={heading} tabIndex={-1}>{outline} outline</h3>
      <Action className={styles.transferCancel} disabled={disabled} aria-label="Cancel outline transfer" onClick={onCancel}>Cancel</Action>
    </div>
    {product === "cat" ? <div className={styles.risks}><h3>Apply to</h3>{children}</div> : children}
    {product === "cat" ? <ForecastDrawingOutlinePicker label="Change outline" disabled={disabled} onCopy={onCopy} /> :
      <div className={styles.risks} role="group" aria-label="Apply at probability">
        <h3>Apply as</h3>
        <div className={styles.riskGrid}>
          {FORECAST_LEVELS[product].map((level) => <ForecastRiskChoice key={level} product={product} level={level} className={styles.transferProbability} disabled={disabled}
            aria-label={"Apply as " + String(level) + "%"} onClick={() => {
              void useForecastDrawingStore.getState().pasteCategoricalOutline(backend, level).then((applied) => {
                if (applied) onApplied(level);
              });
            }}>{level}%</ForecastRiskChoice>)}
        </div>
      </div>}
  </>;
}

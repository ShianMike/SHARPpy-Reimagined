import type { CSSProperties, ReactNode } from "react";
import { cx } from "../../lib/cx";
import type { ForecastProduct } from "../../types/forecastDrawing";
import { Action, type ActionProps } from "../../ui/Controls";
import styles from "./ForecastDrawingTools.module.css";

const CATEGORY_FILLS = [
  "var(--spc-thunderstorms-fill)", "var(--spc-marginal-fill)", "var(--spc-slight-fill)",
  "var(--spc-enhanced-fill)", "var(--spc-moderate-fill)", "var(--spc-high-fill)",
] as const;

// Match the authored contour fills in the Rust SPC outlook palette.
const TORNADO_FILLS: Readonly<Record<number, string>> = {
  2: "var(--spc-marginal-fill)",
  5: "var(--spc-5-percent-fill)",
  10: "var(--spc-slight-fill)",
  15: "var(--spc-moderate-fill)",
  30: "var(--spc-high-fill)",
  45: "var(--spc-60-percent-fill)",
  60: "var(--spc-60-percent-fill)",
};
const HAIL_WIND_FILLS: Readonly<Record<number, string>> = {
  5: "var(--spc-5-percent-fill)",
  15: "var(--spc-slight-fill)",
  30: "var(--spc-moderate-fill)",
  45: "var(--spc-high-fill)",
  60: "var(--spc-60-percent-fill)",
  75: "var(--spc-75-percent-fill)",
  90: "var(--spc-90-percent-fill)",
};

export function ForecastRiskChoice({ product, level, hatched = false, className, children, ...props }: ActionProps & {
  product: ForecastProduct;
  level: number;
  hatched?: boolean;
}): ReactNode {
  const palette = product === "cat" ? CATEGORY_FILLS : product === "torn" ? TORNADO_FILLS : HAIL_WIND_FILLS;
  const fill = hatched ? "var(--text-secondary)" : palette[level];
  return <Action {...props} className={cx(styles.choice, styles.risk, className)}
    style={{ ...props.style, "--risk-fill": fill ?? "var(--text-secondary)" } as CSSProperties}>
    <span className={styles.swatch} data-hatch={hatched ? level : undefined} aria-hidden="true" />
    {children}
  </Action>;
}

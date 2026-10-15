// Text for assistive technology only.

import type { HTMLAttributes, ReactNode } from "react";
import { cx } from "../lib/cx";
import styles from "./VisuallyHidden.module.css";

export function VisuallyHidden({ className, ...rest }: HTMLAttributes<HTMLSpanElement>): ReactNode {
  return <span {...rest} className={cx(styles.hidden, className)} />;
}

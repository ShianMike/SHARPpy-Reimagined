// A small status dot. Decorative: the adjacent text always states the status.

import type { ReactNode } from "react";
import styles from "./StatusDot.module.css";

export type StatusTone = "success" | "warning" | "danger" | "info" | "neutral";

export function StatusDot({ tone }: { tone: StatusTone }): ReactNode {
  return <span className={styles.dot} data-tone={tone} aria-hidden="true" />;
}

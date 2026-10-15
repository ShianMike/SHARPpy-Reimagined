// A quiet empty state for workspaces and panels: a title, a message, and an
// optional action. The title is the largest text in the app (heading size).

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "../lib/cx";
import styles from "./EmptyState.module.css";

export interface EmptyStateProps {
  title: string;
  message?: ReactNode;
  icon?: LucideIcon;
  children?: ReactNode;
  headingLevel?: 2 | 3;
}

export function EmptyState({ title, message, icon: Icon, children, headingLevel = 2 }: EmptyStateProps): ReactNode {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className={styles.empty} data-motion="fade">
      {Icon === undefined ? null : <Icon className={cx("icon", styles.icon)} aria-hidden="true" />}
      <Heading className={styles.title}>{title}</Heading>
      {message === undefined ? null : <div className={styles.message}>{message}</div>}
      {children === undefined ? null : <div className={styles.actions}>{children}</div>}
    </div>
  );
}

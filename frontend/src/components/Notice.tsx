// A notice: a title, a message, an optional muted detail line, and actions.
// "floating" makes it its own glass surface (map notices); "flat" sits inside
// another surface or an opaque workspace.

import type { ReactNode } from "react";
import { Surface } from "../ui/Surface";
import styles from "./Notice.module.css";

export type NoticeTone = "info" | "warning" | "danger" | "neutral";

export interface NoticeProps {
  tone: NoticeTone;
  title: string;
  message?: string;
  detail?: string;
  actions?: ReactNode;
  surface?: "floating" | "flat";
  /** Status for quiet updates; alert only for failures that need attention now. */
  role?: "status" | "alert";
  testId?: string;
  /** Extra content after the detail line, such as a log path. */
  children?: ReactNode;
}

export function Notice({
  tone,
  title,
  message,
  detail,
  actions,
  surface = "flat",
  role = "status",
  testId,
  children,
}: NoticeProps): ReactNode {
  const body = (
    <>
      <p className={styles.title}>{title}</p>
      {message === undefined ? null : <p className={styles.message}>{message}</p>}
      {detail === undefined ? null : <p className={styles.detail}>{detail}</p>}
      {children}
      {actions === undefined ? null : <div className={styles.actions}>{actions}</div>}
    </>
  );
  if (surface === "floating") {
    return (
      <Surface className={styles.notice} data-motion="enter" data-tone={tone} role={role} data-testid={testId}>
        {body}
      </Surface>
    );
  }
  return (
    <div className={styles.notice} data-motion="fade" data-tone={tone} role={role} data-testid={testId}>
      {body}
    </div>
  );
}

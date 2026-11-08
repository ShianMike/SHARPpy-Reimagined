// A compact failure explanation and the recovery the operation supports.
// Keep the producer's message and provenance in Details; raw error.details
// remain in the diagnostic log, never rendered as page content.

import { ChevronDown, LoaderCircle, RotateCcw } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { Notice } from "../../components/Notice";
import type { AnyBackendError } from "../../types/contracts";
import { errorActions, errorRecovery, errorSourceLine } from "./errorPresentation";
import { errorCopy } from "./errorCopy";
import styles from "./BackendErrorNotice.module.css";

export interface BackendErrorNoticeProps {
  error: AnyBackendError;
  onRetry: () => void;
  onRestart: () => void;
  /** True while a retry or restart is running: the buttons are disabled. */
  busy?: boolean;
  /** The developer log, from the health report; null when it is not known. */
  logFile: string | null;
  surface?: "floating" | "flat";
  testId?: string;
  showIcon?: boolean;
  operation?: string;
}

export function BackendErrorNotice({
  error,
  onRetry,
  onRestart,
  busy = false,
  logFile,
  surface = "floating",
  testId = "backend-error",
  showIcon = true,
  operation,
}: BackendErrorNoticeProps): ReactNode {
  const [action, setAction] = useState<"retry" | "restart" | null>(null);
  const actions = errorActions(error.code);
  const context = operation === undefined ? error.context : { ...error.context, operation };
  const copy = errorCopy(error, context);
  const source = errorSourceLine(context);
  const [retryLabel] = errorRecovery(context);
  const buttons =
    actions.retry || actions.restart ? (
      <>
        {actions.retry ? (
          <Action tone="accent" disabled={busy} aria-busy={busy && action === "retry"} onClick={() => { setAction("retry"); onRetry(); }}>
            {busy && action === "retry" ? <><LoaderCircle className="icon-sm" data-loading="true" aria-hidden="true" />Retrying…</> : <><RotateCcw className="icon-sm" aria-hidden="true" />{retryLabel}</>}
          </Action>
        ) : null}
        {actions.restart ? (
          <Action tone={actions.retry ? "quiet" : "accent"} disabled={busy} aria-busy={busy && action === "restart"} onClick={() => { setAction("restart"); onRestart(); }}>
            {busy && action === "restart" ? <><LoaderCircle className="icon-sm" data-loading="true" aria-hidden="true" />Restarting…</> : "Restart service"}
          </Action>
        ) : null}
      </>
    ) : undefined;
  return (
    <Notice
      tone={copy.tone}
      surface={surface}
      role="alert"
      title={copy.title}
      message={copy.message}
      icon={copy.icon}
      {...(buttons === undefined ? {} : { actions: buttons })}
      testId={testId}
      showIcon={showIcon}
    >
      <details className={styles.details}>
        <summary>Details<ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        {copy.message === error.message ? null : <p>{error.message}</p>}
        {source === null ? null : <p>{source}</p>}
        <code>{error.code}{context === undefined ? "" : ` · ${context.operation}`}</code>
        {actions.logPath ? <p className={styles.log} data-testid="error-log-path">
          {logFile === null ? "Recorded in Logs." : `Log: ${logFile}`}
        </p> : null}
      </details>
    </Notice>
  );
}

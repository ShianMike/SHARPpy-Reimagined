// A backend error as a notice (REQUEST §27, DESIGN-contracts C6): what failed
// (the title, from the operation), the producer's message, what was involved
// (a muted provider and resource line), and what the user can do: Retry,
// Restart backend, the configuration instruction (the message itself), or the
// developer log path. details never render; the client already logged them.

import type { ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { Notice } from "../../components/Notice";
import type { AnyBackendError } from "../../types/contracts";
import { errorActions, errorSourceLine, errorTitle, noRetryHint } from "./errorPresentation";
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
}: BackendErrorNoticeProps): ReactNode {
  const actions = errorActions(error.code);
  const source = errorSourceLine(error.context);
  const hint = noRetryHint(actions);
  const buttons =
    actions.retry || actions.restart ? (
      <>
        {actions.retry ? (
          <Action tone="accent" disabled={busy} onClick={onRetry}>
            Retry
          </Action>
        ) : null}
        {actions.restart ? (
          <Action tone={actions.retry ? "quiet" : "accent"} disabled={busy} onClick={onRestart}>
            Restart backend
          </Action>
        ) : null}
      </>
    ) : undefined;
  return (
    <Notice
      tone="danger"
      surface={surface}
      role="alert"
      title={errorTitle(error.context)}
      message={error.message}
      {...(source === null ? {} : { detail: source })}
      {...(buttons === undefined ? {} : { actions: buttons })}
      testId={testId}
      showIcon={showIcon}
    >
      {hint === null ? null : (
        <p className={styles.hint} data-testid="error-no-retry">
          {hint}
        </p>
      )}
      {actions.logPath ? (
        <p className={styles.log} data-testid="error-log-path">
          {logFile === null ? "Details are in the developer log." : `Details are in the log: ${logFile}`}
        </p>
      ) : null}
    </Notice>
  );
}

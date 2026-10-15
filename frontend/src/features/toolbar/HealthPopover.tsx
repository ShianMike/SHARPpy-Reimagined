// The backend status popover (DESIGN-frontend F8, DR6-5): a non-modal dialog
// labelled "Backend status", built on Popover, which owns the focus rules
// (focus on the first button, else the dialog; Esc or Tab out closes it and
// returns focus to the indicator; an outside press closes it in place).
// It shows the app, Python, and sharpmod versions, the active scientific
// backend and any fallback reason, the restart count, the log path, and
// Restart backend where DESIGN-contracts C6 calls for it.

import type { ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { Popover, type PopoverCloseReason } from "../../components/Popover";
import { StatusDot } from "../../components/StatusDot";
import { cx } from "../../lib/cx";
import { failureOf } from "../../lib/backend/errors";
import type { BackendHealth, ScientificBackendStatus } from "../../types/contracts";
import { errorActions } from "../errors/errorPresentation";
import type { HealthView } from "./healthStatus";
import styles from "./HealthPopover.module.css";

export interface HealthPopoverProps {
  open: boolean;
  anchor: HTMLElement | null;
  onClose: (reason: PopoverCloseReason) => void;
  view: HealthView;
  health: BackendHealth | undefined;
  /** The failure of the last health check, if it failed. */
  error: unknown;
  onRestart: () => void;
  restarting: boolean;
}

/** The sentence that explains a state other than ready, or null. */
function problemText(health: BackendHealth | undefined, error: unknown): string | null {
  if (error !== null && error !== undefined) {
    return failureOf(error)?.message ?? "The backend status could not be checked.";
  }
  if (health === undefined) {
    return null;
  }
  const { bridge, scientificBackend } = health;
  switch (bridge.status) {
    case "starting":
      return "The Python backend is starting.";
    case "restarting":
      return `${bridge.lastError.message} Attempt ${String(bridge.attempt)}.`;
    case "unavailable":
      return bridge.error.message;
    case "up":
      if (scientificBackend?.status === "unavailable") {
        return scientificBackend.message;
      }
      return scientificBackend?.status === "unknown" ? scientificBackend.error.message : null;
  }
}

function backendText(status: Extract<ScientificBackendStatus, { status: "ok" }>): string {
  if (status.activeBackend === "rust") {
    return status.rustVersion === null ? "Rust" : `Rust, sharpmod_rs ${status.rustVersion}`;
  }
  return "Python";
}

function canRestart(health: BackendHealth | undefined): boolean {
  return health?.bridge.status === "unavailable" && errorActions(health.bridge.error.code).restart;
}

function Fact({ term, children, mono = false }: { term: string; children: ReactNode; mono?: boolean }): ReactNode {
  return (
    <>
      <dt className={styles.term}>{term}</dt>
      <dd className={cx(styles.value, mono ? styles.mono : undefined)}>{children}</dd>
    </>
  );
}

export function HealthPopover(props: HealthPopoverProps): ReactNode {
  const { open, anchor, onClose, view, health, error, onRestart, restarting } = props;
  const problem = problemText(health, error);
  const scientific = health?.scientificBackend;
  const restarts = health?.bridge.status === "up" ? health.bridge.restarts : 0;
  return (
    <Popover
      open={open}
      anchor={anchor}
      onClose={onClose}
      role="dialog"
      label="Backend status"
      initialFocus="first"
      align="end"
      width="popover"
      testId="health-popover"
    >
      <div className={styles.body}>
        <p className={styles.status}>
          <StatusDot tone={view.tone} />
          <span>{view.label}</span>
        </p>
        {problem === null ? null : <p className={styles.problem}>{problem}</p>}
        {health === undefined ? null : (
          <dl className={styles.facts}>
            <Fact term="App">{`${health.app.name} ${health.app.version} (${health.app.buildProfile})`}</Fact>
            {health.python === null ? null : (
              <>
                <Fact term="Python">{health.python.version}</Fact>
                <Fact term="sharpmod">{health.python.sharpmodVersion}</Fact>
              </>
            )}
            {scientific?.status === "ok" ? (
              <>
                <Fact term="Scientific backend">{backendText(scientific)}</Fact>
                {scientific.fallbackReason === null ? null : (
                  <Fact term="Fallback reason">{scientific.fallbackReason}</Fact>
                )}
              </>
            ) : null}
            <Fact term="Log file" mono>
              {health.app.logFile}
            </Fact>
          </dl>
        )}
        {restarts > 0 ? <p className={styles.note}>{`Backend restarts this session: ${String(restarts)}`}</p> : null}
        {canRestart(health) ? (
          <div className={styles.actions}>
            <Action tone="accent" disabled={restarting} onClick={onRestart}>
              Restart backend
            </Action>
          </div>
        ) : null}
      </div>
    </Popover>
  );
}

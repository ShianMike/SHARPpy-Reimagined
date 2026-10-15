// The toolbar health indicator (DESIGN-frontend F8, DR6-5): a button with
// aria-haspopup="dialog" and aria-expanded, showing a status dot and the C3
// label, that opens HealthPopover. The label never shrinks or hides. The
// popover gets the query result as props, so opening it adds no observer.

import { useState, type ReactNode } from "react";
import { StatusDot } from "../../components/StatusDot";
import { cx } from "../../lib/cx";
import { useBackendHealth } from "../../lib/backend/BackendProvider";
import { useRestartBackend } from "../../lib/backend/useRestartBackend";
import { HealthPopover } from "./HealthPopover";
import { healthView } from "./healthStatus";
import styles from "./HealthIndicator.module.css";

export interface HealthIndicatorProps {
  className?: string | undefined;
}

export function HealthIndicator({ className }: HealthIndicatorProps): ReactNode {
  const health = useBackendHealth();
  const { restart, pending } = useRestartBackend();
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const view = healthView(health.status, health.data);

  return (
    <>
      <button
        ref={setAnchor}
        type="button"
        className={cx(styles.indicator, className)}
        aria-haspopup="dialog"
        aria-expanded={open}
        data-testid="health-indicator"
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <StatusDot tone={view.tone} />
        <span className={styles.label}>{view.label}</span>
      </button>
      <HealthPopover
        open={open}
        anchor={anchor}
        onClose={() => {
          setOpen(false);
        }}
        view={view}
        health={health.data}
        error={health.status === "error" ? health.error : null}
        onRestart={restart}
        restarting={pending}
      />
    </>
  );
}

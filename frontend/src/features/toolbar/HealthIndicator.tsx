import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useBackendHealth } from "../../lib/backend/BackendProvider";
import { useRestartBackend } from "../../lib/backend/useRestartBackend";
import { IconAction } from "../../ui/Controls";
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
      <IconAction
        ref={setAnchor}
        icon={Info}
        label="Backend info"
        size="sm"
        className={cx(styles.indicator, className)}
        data-health-tone={view.tone}
        data-testid="health-indicator"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-description={view.tone === "success" ? undefined : view.label}
        onClick={() => {
          setOpen((value) => !value);
        }}
      />
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

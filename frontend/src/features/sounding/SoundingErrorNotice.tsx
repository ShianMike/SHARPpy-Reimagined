// A failed sounding request as a flat BackendErrorNotice: the C6 action for its
// code, Restart backend wired, and the developer log path from the last health.
import type { ReactNode } from "react";
import { failureOf, transportFailed } from "../../lib/backend/errors";
import { useKnownHealth } from "../../lib/backend/useKnownHealth";
import { useRestartBackend } from "../../lib/backend/useRestartBackend";
import { BackendErrorNotice } from "../errors/BackendErrorNotice";

export interface SoundingErrorNoticeProps {
  error: unknown;
  /** The operation to blame when the error is not a backend error. */
  operation: string;
  onRetry: () => void;
  busy?: boolean;
  testId: string;
}

export function SoundingErrorNotice({ error, operation, onRetry, busy = false, testId }: SoundingErrorNoticeProps): ReactNode {
  const health = useKnownHealth();
  const { restart, pending } = useRestartBackend();
  const failure = failureOf(error) ?? transportFailed(operation);
  const rendering = operation === "render_sounding" || operation === "render_sounding_view";
  return (
    <BackendErrorNotice
      error={rendering && failure.code === "TRANSPORT_FAILED" ? { ...failure,
        message: "The app lost its connection to the sounding service. Try again to reconnect." } : failure}
      onRetry={onRetry}
      onRestart={restart}
      busy={busy || pending}
      logFile={health?.app.logFile ?? null}
      surface="flat"
      showIcon={!rendering}
      testId={testId}
    />
  );
}

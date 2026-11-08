import { Shrink, SquareDashedMousePointer } from "lucide-react";
import type { ReactNode } from "react";
import { failureOf, transportFailed } from "../../lib/backend/errors";
import { useRestartBackend } from "../../lib/backend/useRestartBackend";
import { Action } from "../../ui/Controls";
import { Notice } from "../../components/Notice";
import { BackendErrorNotice } from "../errors/BackendErrorNotice";

export function BoxSoundingIssue({ error, onRetry, onRedraw, onDismiss, busy }: {
  error: unknown; onRetry: () => void; onRedraw: () => void; onDismiss: () => void; busy: boolean;
}): ReactNode {
  const failure = failureOf(error) ?? transportFailed("open_sounding");
  const { restart, pending } = useRestartBackend();
  // Recognise the native extractor's limit messages; leave other failures intact.
  const limit = failure.code === "INVALID_ARGUMENT"
    ? /(?:more than|exceeds the)\s+(\d+)\s+(native[- ]columns?|square-kilometre)/i.exec(failure.message) : null;
  if (limit !== null) return <Notice tone="warning" title="Box is too large" icon={Shrink} role="alert" testId="box-error"
    message={`Maximum ${Number(limit[1]).toLocaleString("en-US")} ${limit[2] === "square-kilometre" ? "km²" : "grid columns"}.`}
    actions={<Action tone="accent" onClick={onRedraw}>
      <SquareDashedMousePointer className="icon" aria-hidden="true" />Draw a smaller box
    </Action>} />;
  return <>
    <BackendErrorNotice error={failure} operation="open_sounding" onRetry={onRetry} onRestart={restart}
      busy={busy || pending} logFile={null} testId="box-error" surface="flat" />
    <Action onClick={onDismiss} disabled={busy || pending}>Dismiss</Action>
  </>;
}

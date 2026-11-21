import { ChevronRight, RotateCcw } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { Notice } from "../../components/Notice";
import { useSoundingRecoveryStore } from "../../stores/soundingRecoveryStore";
import { useUiStore } from "../../stores/uiStore";
import { Action } from "../../ui/Controls";
import type { RecoverySession } from "./recoverySession";
import { forecastLabel, formatUtc } from "./soundingTime";
import styles from "./SoundingRecovery.module.css";

const SOURCE_LABELS = { observed: "Observed", model: "Model", era5: "ERA5 reanalysis", file: "Local file" };
const RECOVERY_STATUS = {
  starting: "Starting autosave…",
  idle: "Autosave on",
  saving: "Saving session…",
  saved: "Session saved locally",
  restoring: "Restoring session…",
};

export function SoundingRecoveryPrompt(): ReactNode {
  const previous = useSoundingRecoveryStore(state => state.previous);
  const prompt = useSoundingRecoveryStore(state => state.prompt);
  const whatsNewOpen = useUiStore(state => state.whatsNewOpen);
  return previous === null || !prompt || whatsNewOpen ? null : <RecoveryDialog session={previous} />;
}

function RecoveryDialog({ session }: { session: RecoverySession }): ReactNode {
  const { status, restore, discard, message } = useSoundingRecoveryStore();
  const [anchor] = useState(() => document.activeElement instanceof HTMLElement ? document.activeElement : document.body);
  const id = useId();
  const savedAt = new Date(session.savedAt);
  const count = session.open.length;
  const restoring = status === "restoring";
  const busy = restoring || status === "saving";
  const later = (): void => { if (!busy) useSoundingRecoveryStore.setState({ prompt: false }); };
  return <Popover open anchor={anchor} role="dialog" labelledBy={`${id}-title`} initialFocus="first" movable backdrop
    onClose={later} className={styles.prompt}>
    <header className={styles.header} data-popover-drag>
      <h2 id={`${id}-title`}>Restore sounding session?</h2>
      <p className={styles.sessionMeta}>
        <span>Saved <time dateTime={savedAt.toJSON()}>{savedAt.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</time></span>
        <span>{session.mode === "winter" ? "Winter view" : "Severe view"}</span>
      </p>
    </header>
    <div className={styles.body}>
      <div className={styles.listHeading}>
        <h3 id={`${id}-soundings`}>{count} {count === 1 ? "sounding" : "soundings"}</h3>
        <span>Valid time (UTC)</span>
      </div>
      <ul className={styles.list} aria-labelledby={`${id}-soundings`} tabIndex={0}>
        {session.open.map(item => {
          const active = item.soundingId === session.activeId;
          const validTime = item.times === undefined ? item.validTime : item.times[session.timeIndices[item.soundingId] ?? 0] ?? null;
          const source = item.kind === "model" && item.forecastHour !== null ? forecastLabel(item.forecastHour)
            : item.kind === "observed" ? item.providerName ?? SOURCE_LABELS.observed : SOURCE_LABELS[item.kind];
          return <li key={item.soundingId} className={styles.profile} data-last-viewed={active}>
            <div>
              <strong className={styles.profileTitle}>{item.title}</strong>
              <div className={styles.profileMeta}>
                <span>{source}</span>
                {active ? <span className={styles.lastViewed}>Last viewed</span> : null}
              </div>
            </div>
            <span className={styles.profileTime}>
              {validTime === null ? "Undated" : <time dateTime={validTime}>{formatUtc(validTime)}</time>}
            </span>
          </li>;
        })}
      </ul>
    </div>
    <footer className={styles.footer}>
      {status === "error" ? <Notice tone="danger" role="alert" title="Couldn’t update the saved session" message={message ?? "Your saved copy is still available. Try again."} /> : null}
      {busy ? <p className={styles.detail} role="status">{message ?? (restoring ? "Restoring saved profiles…" : "Updating the saved copy…")}</p> : null}
      <div className={styles.actions}>
        <Action className={styles.discard} disabled={busy} onClick={() => { void discard(); }} aria-description="Delete the saved copy from this device">Discard copy</Action>
        <div className={styles.primaryActions}>
          <Action data-autofocus disabled={busy} onClick={() => { later(); if (anchor.isConnected) anchor.focus(); }}>Not now</Action>
          <Action disabled={busy} tone="accent" onClick={() => { void restore(); }}>{restoring ? "Restoring…" : "Restore session"}</Action>
        </div>
      </div>
    </footer>
  </Popover>;
}

export function SoundingRecoveryStatus(): ReactNode {
  const { status, previous, prompt, message, retrySave } = useSoundingRecoveryStore();
  const busy = status === "restoring";
  return <div className={styles.status}>
    {previous === null ? null : <Action className={styles.restoreCard} disabled={busy} aria-label="Restore previous session" aria-haspopup="dialog" aria-expanded={prompt}
      onClick={() => { useSoundingRecoveryStore.setState({ prompt: true }); }}>
      <span className={styles.statusIcon}><RotateCcw className="icon-sm" aria-hidden="true" /></span>
      <span className={styles.statusText}>
        <strong>Restore previous session</strong>
        <span className={styles.detail}>{previous.open.length} saved {previous.open.length === 1 ? "sounding" : "soundings"}</span>
      </span>
      <ChevronRight className="icon-sm" aria-hidden="true" />
    </Action>}
    {status === "error" || status === "unavailable" ? <Notice tone="danger" title="Recovery copy unavailable" message={message ?? "Keep this app open and retry saving your session."}
      actions={status === "error" && retrySave !== null ? <Action onClick={retrySave}>Retry recovery save</Action> : undefined} />
      : previous !== null && status === "idle" ? null : <p className={styles.saveDetail} role="status">
        {status === "saved" && previous !== null ? "Current session saved" : RECOVERY_STATUS[status]}
      </p>}
  </div>;
}

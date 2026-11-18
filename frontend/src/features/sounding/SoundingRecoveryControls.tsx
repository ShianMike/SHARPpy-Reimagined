import { Check, ChevronRight, CloudLightning, CloudSun, FileText, HardDrive, History, RadioTower, RotateCcw, Snowflake } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { Notice } from "../../components/Notice";
import { useSoundingRecoveryStore } from "../../stores/soundingRecoveryStore";
import { useUiStore } from "../../stores/uiStore";
import { Action } from "../../ui/Controls";
import { soundingMeta } from "./OpenSoundings";
import type { RecoverySession } from "./recoverySession";
import styles from "./SoundingRecovery.module.css";

const SOURCE_ICONS = { observed: RadioTower, model: CloudSun, era5: History, file: FileText };

export function SoundingRecoveryPrompt(): ReactNode {
  const previous = useSoundingRecoveryStore(state => state.previous);
  const prompt = useSoundingRecoveryStore(state => state.prompt);
  const whatsNewOpen = useUiStore(state => state.whatsNewOpen);
  return previous === null || !prompt || whatsNewOpen ? null : <RecoveryDialog session={previous} />;
}

function RecoveryDialog({ session }: { session: RecoverySession }): ReactNode {
  const { status, restore, discard, message } = useSoundingRecoveryStore();
  const [anchor] = useState(() => document.activeElement instanceof HTMLElement ? document.activeElement : document.body);
  const savedAt = new Date(session.savedAt);
  const winter = session.mode === "winter";
  const ModeIcon = winter ? Snowflake : CloudLightning;
  const busy = status === "restoring";
  const later = (): void => { if (!busy) useSoundingRecoveryStore.setState({ prompt: false }); };
  return <Popover open anchor={anchor} role="dialog" label="Restore previous session?" initialFocus="first" movable backdrop
    onClose={later} className={styles.prompt}>
    <header className={styles.header} data-popover-drag>
      <span className={styles.headerIcon}><History className="icon" aria-hidden="true" /></span>
      <div>
        <h2>Restore previous session?</h2>
        <p className={styles.detail}>Saved <time dateTime={savedAt.toJSON()}>{savedAt.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</time></p>
      </div>
    </header>
    <div className={styles.body} role="region" aria-label="Saved session details">
      <p className={styles.intro}>Resume with your selected time, profile edits, and undo history.</p>
      <div className={styles.profiles}>
        <div className={styles.listHeading}>
          <h3>Saved soundings <span className={styles.count}>{session.open.length}</span></h3>
          <span className={styles.mode}><ModeIcon className="icon-sm" aria-hidden="true" />{winter ? "Winter view" : "Severe view"}</span>
        </div>
        <ul className={styles.list} aria-label="Saved soundings" tabIndex={0}>
          {session.open.map(item => {
            const Icon = SOURCE_ICONS[item.kind];
            const active = item.soundingId === session.activeId;
            const validTime = item.times === undefined ? item.validTime : item.times[session.timeIndices[item.soundingId] ?? 0] ?? null;
            const detail = soundingMeta({ ...item, validTime });
            return <li key={item.soundingId} className={styles.profile} data-last-viewed={active}>
              <Icon className="icon-sm" aria-hidden="true" />
              <strong className={styles.profileTitle}>{item.title}</strong>
              <span className={styles.profileDetail}>{detail}</span>
              {active ? <span className={styles.lastViewed}>Last viewed</span> : null}
            </li>;
          })}
        </ul>
      </div>
      <p className={styles.storage}><HardDrive className="icon-sm" aria-hidden="true" />Stored on this device, separate from the weather cache.</p>
    </div>
    <footer className={styles.footer}>
      {status === "error" ? <Notice tone="danger" role="alert" title="Session could not be restored" message={message ?? "Your saved recovery copy is still available. Try again."} /> : null}
      {busy ? <p className={styles.detail} role="status">{message ?? "Restoring saved profiles…"}</p> : null}
      <div className={styles.actions}>
        <Action className={styles.discard} disabled={busy} onClick={() => { void discard(); }} aria-description="Discard the previous recovery copy">Discard copy</Action>
        <div className={styles.primaryActions}>
          <Action data-autofocus disabled={busy} onClick={() => { later(); if (anchor.isConnected) anchor.focus(); }}>Not now</Action>
          <Action disabled={busy} tone="accent" onClick={() => { void restore(); }}><RotateCcw className="icon-sm" aria-hidden="true" />{busy ? "Restoring…" : "Restore session"}</Action>
        </div>
      </div>
    </footer>
  </Popover>;
}

export function SoundingRecoveryStatus(): ReactNode {
  const { status, previous, prompt, message, retrySave } = useSoundingRecoveryStore();
  const saved = status === "saved";
  const busy = status === "restoring";
  const StatusIcon = saved ? Check : History;
  const detail = saved ? previous === null ? "Saved on this device" : "Current session saved"
    : status === "saving" ? "Saving recovery copy…"
      : busy ? "Restoring previous session…"
        : status === "starting" ? "Preparing recovery…" : "Open soundings are saved here.";
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
      : previous === null ? <div className={styles.statusRow} data-saved={saved}>
        <span className={styles.statusIcon}><StatusIcon className="icon-sm" aria-hidden="true" /></span>
        <div className={styles.statusText}><strong>Session recovery</strong><span className={styles.detail} role="status">{detail}</span></div>
      </div> : status === "idle" ? null : <p className={styles.saveDetail} data-saved={saved} role="status"><StatusIcon className="icon-sm" aria-hidden="true" />{detail}</p>}
  </div>;
}

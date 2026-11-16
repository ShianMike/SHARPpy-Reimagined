import { RotateCcw } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { Notice } from "../../components/Notice";
import { useSoundingRecoveryStore } from "../../stores/soundingRecoveryStore";
import { useUiStore } from "../../stores/uiStore";
import { Action } from "../../ui/Controls";
import styles from "./SoundingRecovery.module.css";

export function SoundingRecoveryPrompt(): ReactNode {
  const { previous, prompt, status, restore, discard, message } = useSoundingRecoveryStore();
  const whatsNewOpen = useUiStore(state => state.whatsNewOpen);
  const [anchor] = useState(() => document.body);
  if (previous === null || !prompt || whatsNewOpen) return null;
  const busy = status === "restoring";
  const later = (): void => { if (!busy) useSoundingRecoveryStore.setState({ prompt: false }); };
  return <Popover open anchor={anchor} role="dialog" label="Restore previous session?" initialFocus="first" movable backdrop
    onClose={later} className={styles.prompt}>
    <h2 data-popover-drag>Restore previous session?</h2>
    <p>{previous.open.length} open {previous.open.length === 1 ? "sounding" : "soundings"} saved {new Date(previous.savedAt).toLocaleString()}.</p>
    <p>Recover profile edits, undo history, the selected time, and your {previous.mode === "winter" ? "Winter" : "Severe"} view.</p>
    <ul className={styles.list}>{previous.open.map(item => <li key={item.soundingId}>{item.title}</li>)}</ul>
    {status === "error" ? <Notice tone="danger" role="alert" title="Session could not be restored" message={message ?? "Your saved recovery copy is still available. Try again."} /> : null}
    {busy ? <p role="status">{message ?? "Restoring saved profiles…"}</p> : null}
    <div className={styles.actions}>
      <Action disabled={busy} onClick={later}>Not now</Action>
      <Action disabled={busy} onClick={() => { void discard(); }}>Start fresh</Action>
      <Action disabled={busy} tone="accent" onClick={() => { void restore(); }}><RotateCcw className="icon-sm" />{busy ? "Restoring…" : "Restore session"}</Action>
    </div>
    <p className={styles.hint}>Recovery stays on this device and is separate from the weather cache.</p>
  </Popover>;
}

export function SoundingRecoveryStatus(): ReactNode {
  const { status, previous, message, retrySave } = useSoundingRecoveryStore();
  return <div className={styles.status}>
    {previous === null ? null : <Action onClick={() => { useSoundingRecoveryStore.setState({ prompt: true }); }}><RotateCcw className="icon-sm" />Restore previous session</Action>}
    {status === "error" || status === "unavailable" ? <Notice tone="danger" title="Recovery copy unavailable" message={message ?? "Keep this app open and retry saving your session."}
      actions={status === "error" && retrySave !== null ? <Action onClick={retrySave}>Retry recovery save</Action> : undefined} />
      : <p role={status === "saved" || status === "saving" || status === "restoring" ? "status" : undefined} className={styles.hint}>{status === "saved" ? "Recovery copy saved on this device" : status === "saving" ? "Saving recovery copy…"
        : status === "restoring" ? "Restoring previous session…" : status === "starting" ? "Preparing session recovery…" : "Open soundings are saved for recovery on this device."}</p>}
  </div>;
}

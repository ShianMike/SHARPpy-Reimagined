import { useRef, useState, type ReactNode } from "react";
import { resetAllSettings } from "../../stores/preferences";
import { Action } from "../../ui/Controls";
import styles from "./SettingsView.module.css";

export function WorkspaceSettings(): ReactNode {
  const [confirm, setConfirm] = useState(false);
  const [reset, setReset] = useState(false);
  const resetButton = useRef<HTMLButtonElement>(null);
  return <div className={styles.workspace}>
    <section className={styles.section}>
      <h3 className={styles.legend}>Saved automatically</h3>
      <p className={styles.hint}>Your map setup, layers and preferences are remembered on this device.</p>
      <p className={styles.workspaceNote}>Each session restores your setup and opens the model’s latest suggested run.</p>
    </section>
    <section className={styles.section}>
      <h3 className={styles.legend}>Restore default settings</h3>
      <p className={styles.hint}>Reset the map setup and app preferences for all panels.</p>
      <p className={styles.workspaceNote}>Keeps your files, open soundings, cached data and map location.</p>
      <div className={styles.updateActions}><Action ref={resetButton} aria-expanded={confirm} onClick={() => { setReset(false); setConfirm(true); }}>Reset all settings…</Action></div>
      {confirm ? <div className={styles.cacheConfirm}>
        <p>Reset all settings to their defaults?</p>
        <div className={styles.updateActions}>
          <Action onClick={() => { setConfirm(false); resetButton.current?.focus(); }}>Cancel</Action>
          <Action tone="accent" onClick={() => { resetAllSettings(); setConfirm(false); setReset(true); resetButton.current?.focus(); }}>Reset now</Action>
        </div>
      </div> : null}
      {reset ? <p className={styles.storageFeedback} role="status">Settings reset to defaults.</p> : null}
    </section>
  </div>;
}

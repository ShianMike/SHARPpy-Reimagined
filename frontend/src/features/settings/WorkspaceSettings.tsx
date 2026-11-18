import { Check, Map, RotateCcw, Settings2 } from "lucide-react";
import { useId, useRef, useState, type ReactNode } from "react";
import { useMapStore } from "../../stores/mapStore";
import { resetAllSettings } from "../../stores/preferences";
import { useUiStore } from "../../stores/uiStore";
import { Action } from "../../ui/Controls";
import styles from "./WorkspaceSettings.module.css";

export function WorkspaceSettings(): ReactNode {
  const [confirm, setConfirm] = useState(false);
  const [reset, setReset] = useState(false);
  const resetButton = useRef<HTMLButtonElement>(null);
  const confirmationId = useId();
  const panelLayout = useMapStore((state) => state.panelLayout);
  const theme = useUiStore((state) => state.theme);
  return <div className={styles.workspace}>
    <section className={styles.saved} aria-labelledby="workspace-saved-title">
      <header className={styles.heading}>
        <div><h3 id="workspace-saved-title">Saved automatically</h3><p>Your workspace is remembered here.</p></div>
        <span className={styles.badge}>On this device</span>
      </header>
      <dl className={styles.summary}>
        <div><Map className="icon" aria-hidden="true" /><dt><strong>Map setup</strong><span>Panels, layers & overlays</span></dt><dd>{String(panelLayout)} {panelLayout === 1 ? "panel" : "panels"}</dd></div>
        <div><Settings2 className="icon" aria-hidden="true" /><dt><strong>App preferences</strong><span>Appearance, units & playback</span></dt><dd>{theme === "system" ? "System" : theme === "dark" ? "Dark" : "Light"} theme</dd></div>
      </dl>
      <p className={styles.note}>Reopen with your setup and the model’s latest suggested run.</p>
    </section>
    <section className={styles.reset} aria-labelledby="workspace-reset-title">
      <header className={styles.heading}>
        <span className={styles.icon}><RotateCcw className="icon" aria-hidden="true" /></span>
        <div><h3 id="workspace-reset-title">Restore default settings</h3><p>Reset preferences and setup for every panel.</p></div>
      </header>
      <div className={styles.resetFooter}>
        <p className={styles.kept}><strong>Your work stays</strong><span>Files, open soundings, cache and map location.</span></p>
        <Action ref={resetButton} className={styles.resetAction} aria-expanded={confirm} aria-controls={confirm ? confirmationId : undefined} onClick={() => { setReset(false); setConfirm(true); }}>Reset all settings…</Action>
      </div>
      {confirm ? <div id={confirmationId} className={styles.confirm} role="group" aria-labelledby={`${confirmationId}-title`}>
        <p id={`${confirmationId}-title`}>Reset all settings to their defaults?</p>
        <div className={styles.actions}>
          <Action onClick={() => { setConfirm(false); resetButton.current?.focus(); }}>Cancel</Action>
          <Action tone="accent" onClick={() => { resetAllSettings(); setConfirm(false); setReset(true); resetButton.current?.focus(); }}>Reset now</Action>
        </div>
      </div> : null}
      {reset ? <p className={styles.feedback} role="status"><Check className="icon-sm" aria-hidden="true" />Settings reset to defaults.</p> : null}
    </section>
  </div>;
}

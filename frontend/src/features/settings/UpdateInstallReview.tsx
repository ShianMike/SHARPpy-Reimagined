import { useState, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { useAppStore } from "../../stores/appStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { useUpdateStore } from "../../stores/updateStore";
import { Action } from "../../ui/Controls";
import { hasLocalWork } from "../sounding/viewHistory";
import styles from "./SettingsView.module.css";

export function UpdateInstallReview(): ReactNode {
  const { installReview, update, resolveInstallReview } = useUpdateStore();
  const storedWork = useSoundingStore((state) => Object.values(state.views).some(hasLocalWork));
  const localWork = storedWork || installReview === "install";
  const [anchor] = useState(() => document.activeElement instanceof HTMLElement ? document.activeElement : document.body);
  const close = (): void => {
    void resolveInstallReview(false);
    requestAnimationFrame(() => { if (anchor.isConnected) anchor.focus(); });
  };
  const title = localWork ? "Discard sounding work and update?" : "Install app update?";
  return <Popover open anchor={anchor} movable role="dialog" label={title} initialFocus="first" className={styles.whatsNew} onClose={close}>
    <header className={styles.releaseHeader}><div><p className={styles.updateVersion}>Version {update?.version}</p><h2>{title}</h2></div></header>
    <div className={styles.releaseNotes}>
      <p className={styles.releaseIntro}>{installReview === "install"
        ? "The update is downloaded. Local sounding work changed during the download. Review it again before installing."
        : "The app closes to install this update, then restarts."}</p>
      <div><strong>Kept on this device</strong><p className={styles.hint}>Saved app preferences, source files and cached weather data on disk are kept. The disk cache does not save your open sounding session.</p></div>
      <div><strong>Lost when the app closes</strong><p className={styles.hint}>All open soundings, profile edits, chart changes and undo/redo history are held in memory and will be lost. Unapplied level inputs and unfinished recalculations will also be lost. Reopen or retrieve the original soundings after updating.</p></div>
      {localWork ? <p role="alert">Unsaved sounding work was found. This may include edits, unfinished inputs, recalculations and retained history.</p> : null}
      <p className={styles.hint}>To keep a picture, return to Soundings and use Export PNG in the sounding controls. A PNG cannot restore editable profiles or undo/redo history.</p>
    </div>
    <footer className={styles.releaseFooter}>
      <Action data-autofocus onClick={close}>Cancel</Action>
      <Action onClick={() => {
        void resolveInstallReview(false);
        useAppStore.getState().setActiveWorkspace("sounding");
        requestAnimationFrame(() => { document.querySelector<HTMLElement>('[data-testid="sounding-workspace"]')?.focus(); });
      }}>Return to soundings</Action>
      <Action tone="accent" onClick={() => { void resolveInstallReview(true); }}>{localWork ? "Discard work & install update" : "Install update & restart"}</Action>
    </footer>
  </Popover>;
}

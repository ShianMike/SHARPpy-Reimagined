import type { ReactNode } from "react";
import { version } from "../../../package.json";
import { useUpdateStore } from "../../stores/updateStore";
import { useUiStore } from "../../stores/uiStore";
import { BrandMark } from "../../ui/BrandMark";
import { Action } from "../../ui/Controls";
import styles from "./SettingsView.module.css";
import { ReleaseNotes } from "./ReleaseNotes";

export function UpdateSettings(): ReactNode {
  const { status, update, downloaded, total, error, check, install } = useUpdateStore();
  const desktop = "__TAURI_INTERNALS__" in window;
  const busy = status === "checking" || status === "downloading" || status === "installing";
  const title = !desktop ? "Windows app required" : error !== null ? "Update failed"
    : status === "checking" ? "Checking for updates…"
    : status === "downloading" ? `Downloading version ${update?.version ?? ""}…`
    : status === "installing" ? "Installing update…"
    : update !== null ? `Version ${update.version} is available`
    : status === "current" ? "You’re up to date"
    : "Ready to check for updates";

  return <div className={styles.updates}>
    <div className={styles.updateIdentity}>
      <BrandMark className={styles.updateBrand} />
      <div><h3>SHARPpy Reimagined</h3><p className={styles.updateVersion}>Current version: {version}</p></div>
    </div>
    <div className={styles.updateStatus}>
      <div className={styles.updateMessage} role={error === null ? "status" : "alert"}>
        <p className={styles.updateTitle}>{title}</p>
        <p>{!desktop ? "Check for updates in the installed Windows app." : error ?? (update === null
          ? "New releases are installed only when you choose."
          : "Finish your work first. The app restarts to install this update; saved preferences are kept.")}</p>
      </div>
      <div className={styles.updateActions}>
        <Action tone={update === null ? "accent" : "quiet"} disabled={!desktop || busy} onClick={() => { void check(); }}>{status === "checking" ? "Checking…" : "Check for updates"}</Action>
        {update === null ? null : <Action tone="accent" disabled={busy} onClick={() => { void install(); }}>Install update</Action>}
      </div>
    </div>
    <p className={styles.updateSchedule}>Automatic checks run at 00:00 and 12:00 UTC, once per window on this device. If the app is closed or offline, it checks when next available. You can check manually anytime and choose when to install.</p>
    {status === "downloading" ? <div className={styles.updateProgress}>
      <progress aria-label="Update download" value={total === undefined ? undefined : downloaded} max={total ?? 1} />
      {total === undefined ? null : <span>{Math.min(100, Math.floor(downloaded / total * 100))}%</span>}
    </div> : null}
    {update?.body ? <section className={styles.releaseSummary} aria-label="Available update highlights"><h3>Coming in version {update.version}</h3><p className={styles.availableNotes}>{update.body}</p></section> : null}
    <section className={styles.releaseSummary} aria-labelledby="installed-release-title">
      <div className={styles.releaseHeader}><h3 id="installed-release-title">What’s new in {version}</h3><Action aria-haspopup="dialog" onClick={() => { useUiStore.getState().setWhatsNewOpen(true); }}>View highlights</Action></div>
      <ReleaseNotes version={version} />
    </section>
  </div>;
}

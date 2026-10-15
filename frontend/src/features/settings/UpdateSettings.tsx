import type { ReactNode } from "react";
import { version } from "../../../package.json";
import { useUpdateStore } from "../../stores/updateStore";
import { BrandMark } from "../../ui/BrandMark";
import { Action } from "../../ui/Controls";
import styles from "./SettingsView.module.css";

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
    {status === "downloading" ? <div className={styles.updateProgress}>
      <progress aria-label="Update download" value={total === undefined ? undefined : downloaded} max={total ?? 1} />
      {total === undefined ? null : <span>{Math.min(100, Math.floor(downloaded / total * 100))}%</span>}
    </div> : null}
    {update?.body ? <details><summary>What’s new</summary><p className={styles.releaseNotes}>{update.body}</p></details> : null}
    <p className={styles.updateSchedule}>The Windows app checks on launch, hourly, and when your connection returns. You choose when to install.</p>
  </div>;
}

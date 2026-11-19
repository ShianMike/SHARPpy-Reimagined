import { useState, type MouseEvent, type ReactNode } from "react";
import { version } from "../../../package.json";
import { useAppStore, type AppPlatform } from "../../stores/appStore";
import { useUpdateStore } from "../../stores/updateStore";
import { useUiStore } from "../../stores/uiStore";
import { BrandMark } from "../../ui/BrandMark";
import { Action } from "../../ui/Controls";
import actionStyles from "../../ui/Controls.module.css";
import styles from "./SettingsView.module.css";
import { ReleaseNotes } from "./ReleaseNotes";
import { Notice } from "../../components/Notice";
import { Download, ExternalLink } from "lucide-react";

const RELEASES_URL = "https://github.com/ShianMike/SHARPpy-Reimagined/releases";
type ManualPlatform = "linux" | "macos";

function ManualUpdates({ platform }: { platform: ManualPlatform }): ReactNode {
  const [openFailed, setOpenFailed] = useState(false);
  const openReleases = (event: MouseEvent<HTMLAnchorElement>): void => {
    event.preventDefault();
    setOpenFailed(false);
    void import("../../lib/backend/tauriTransport").then(({ openUpdateReleases }) => openUpdateReleases())
      .catch(() => { setOpenFailed(true); });
  };
  return <>
    <div className={styles.updateStatus}>
      <div className={styles.updateMessage} role="status">
        <p className={styles.updateTitle}>Manual updates</p>
        <p>{platform === "linux"
          ? "Download the Linux x64 .deb from GitHub Releases and install it with your package manager."
          : "Download the macOS .dmg for your Mac from GitHub Releases. Open it and move the app to Applications."}</p>
      </div>
      <a className={actionStyles.action} data-tone="accent" href={RELEASES_URL} target="_blank" rel="noopener noreferrer" onClick={openReleases}>
        GitHub Releases <ExternalLink className="icon-sm" aria-hidden="true" />
      </a>
    </div>
    {openFailed ? <p role="alert" className={styles.wrap}>Couldn’t open GitHub Releases. In your browser, go to {RELEASES_URL}.</p> : null}
  </>;
}

function InAppUpdates({ platform }: { platform: Exclude<AppPlatform, ManualPlatform> }): ReactNode {
  const { status, update: availableUpdate, downloaded, total, error, check, install } = useUpdateStore();
  const windows = platform === "windows";
  const update = windows ? availableUpdate : null;
  const busy = status === "checking" || status === "reviewing" || status === "downloading" || status === "installing";
  const title = platform === "browser" ? "Windows app required"
    : platform === "detecting" ? "Checking update availability…"
    : platform === "unknown" ? "Update availability unknown"
    : status === "checking" ? "Checking for updates…"
    : status === "reviewing" ? "Review your work before updating"
    : status === "downloading" ? `Downloading version ${update?.version ?? ""}…`
    : status === "installing" ? "Installing update…"
    : update !== null ? `Version ${update.version} is available`
    : status === "current" ? "You’re up to date"
    : "Ready to check for updates";

  const hint = platform === "browser" ? "The Windows app checks at 00:00 and 12:00 UTC, or when next opened online. You choose when to install."
    : platform === "unknown" ? "Reopen the app to check update availability again."
    : platform === "detecting" ? null
    : update === null ? "Checks at 00:00 and 12:00 UTC, or when next opened online. You choose when to install."
    : "Updating keeps saved preferences and disk caches. Saved sounding recovery copies can be restored after reopening. Let recovery finish saving and review unfinished edits before installing.";

  return <>
    {windows && error !== null ? <Notice tone="danger" role="alert" title={update === null ? "Update check failed" : "Update could not be installed"} icon={Download}
      message={error} testId="update-error" actions={<>
        <Action disabled={busy} onClick={() => { void check(); }}>Check for updates</Action>
        {update === null ? null : <Action tone="accent" disabled={busy} onClick={install}>Retry update</Action>}
      </>} /> : <div className={styles.updateStatus}>
      <div className={styles.updateMessage} role="status">
        <p className={styles.updateTitle}>{title}</p>
        {hint === null ? null : <p>{hint}</p>}
      </div>
      <div className={styles.updateActions}>
        <Action tone={update === null ? "accent" : "quiet"} disabled={!windows || busy} onClick={() => { void check(); }}>{windows && status === "checking" ? "Checking…" : "Check for updates"}</Action>
        {update === null ? null : <Action tone="accent" disabled={busy} onClick={install}>Install update</Action>}
      </div>
    </div>}
    {windows && status === "downloading" ? <div className={styles.updateProgress}>
      <progress aria-label="Update download" value={total === undefined ? undefined : downloaded} max={total ?? 1} />
      {total === undefined ? null : <span>{Math.min(100, Math.floor(downloaded / total * 100))}%</span>}
    </div> : null}
    {update?.body ? <section className={styles.releaseSummary} aria-label="Available update highlights"><h3>Coming in version {update.version}</h3><p className={styles.availableNotes}>{update.body}</p></section> : null}
  </>;
}

export function UpdateSettings(): ReactNode {
  const platform = useAppStore((state) => state.platform);
  return <div className={styles.updates}>
    <div className={styles.updateIdentity}>
      <BrandMark className={styles.updateBrand} />
      <div><h3>SHARPpy Reimagined</h3><p className={styles.updateVersion}>Current version: {version}</p></div>
    </div>
    {platform === "linux" || platform === "macos" ? <ManualUpdates platform={platform} /> : <InAppUpdates platform={platform} />}
    <section className={styles.releaseSummary} aria-labelledby="installed-release-title">
      <div className={styles.releaseHeader}><h3 id="installed-release-title">What’s new in {version}</h3><Action aria-haspopup="dialog" onClick={() => { useUiStore.getState().setWhatsNewOpen(true); }}>View highlights</Action></div>
      <ReleaseNotes version={version} />
    </section>
  </div>;
}

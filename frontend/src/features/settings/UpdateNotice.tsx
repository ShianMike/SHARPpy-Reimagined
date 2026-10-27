import { useEffect, type ReactNode } from "react";
import { Notice } from "../../components/Notice";
import { AUTOMATIC_UPDATE_INTERVAL_MS, useUpdateStore } from "../../stores/updateStore";
import { Action } from "../../ui/Controls";
import { UpdateInstallReview } from "./UpdateInstallReview";

export function UpdateNotice(): ReactNode {
  const { status, installReview, update, downloaded, total, error, dismissedVersion, dismiss, check, install } = useUpdateStore();
  const desktop = "__TAURI_INTERNALS__" in window;
  useEffect(() => {
    if (!desktop) return;
    const checkAutomatically = (): void => { if (navigator.onLine) void check(true); };
    let timer: number;
    const schedule = (): void => {
      checkAutomatically();
      timer = window.setTimeout(schedule, AUTOMATIC_UPDATE_INTERVAL_MS - Date.now() % AUTOMATIC_UPDATE_INTERVAL_MS);
    };
    const visible = (): void => { if (document.visibilityState === "visible") checkAutomatically(); };
    schedule();
    window.addEventListener("online", checkAutomatically);
    document.addEventListener("visibilitychange", visible);
    return () => { window.clearTimeout(timer); window.removeEventListener("online", checkAutomatically); document.removeEventListener("visibilitychange", visible); };
  }, [check, desktop]);

  if (!desktop) return null;
  // This global host also reviews installs requested from Settings, even after Later.
  const review = installReview === null ? null : <UpdateInstallReview />;
  if (update === null || update.version === dismissedVersion) return review;
  const busy = status === "checking" || status === "reviewing" || status === "downloading" || status === "installing";
  const title = error !== null ? "Update failed"
    : status === "downloading" ? `Downloading update${total === undefined ? "…" : `: ${String(Math.min(100, Math.floor(downloaded / total * 100)))}%`}`
    : status === "installing" ? "Installing update…" : `Version ${update.version} is available`;
  return <>{review}<Notice surface="floating" tone={error === null ? "info" : "danger"} role={error === null ? "status" : "alert"}
    testId="app-update-notice" title={title}
    message={error ?? "Updating keeps saved preferences and disk caches, but closes open soundings and discards their edits and history."}
    {...(error === null ? { detail: "Review your work before installing." } : {})}
    actions={<>
      <Action disabled={busy} onClick={dismiss}>Later</Action>
      <Action tone="accent" disabled={busy} onClick={install}>Update now</Action>
    </>} /></>;
}

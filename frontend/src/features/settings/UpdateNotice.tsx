import { useEffect, type ReactNode } from "react";
import { Notice } from "../../components/Notice";
import { AUTOMATIC_UPDATE_INTERVAL_MS, useUpdateStore } from "../../stores/updateStore";
import { Action } from "../../ui/Controls";

export function UpdateNotice(): ReactNode {
  const { status, update, downloaded, total, error, dismissedVersion, dismiss, check, install } = useUpdateStore();
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

  if (!desktop || update === null || update.version === dismissedVersion) return null;
  const busy = status === "checking" || status === "downloading" || status === "installing";
  const title = error !== null ? "Update failed"
    : status === "downloading" ? `Downloading update${total === undefined ? "…" : `: ${String(Math.min(100, Math.floor(downloaded / total * 100)))}%`}`
    : status === "installing" ? "Installing update…" : `Version ${update.version} is available`;
  return <Notice surface="floating" tone={error === null ? "info" : "danger"} role={error === null ? "status" : "alert"}
    testId="app-update-notice" title={title}
    message={error ?? "The app restarts to finish updating. Your saved preferences are kept."}
    {...(error === null ? { detail: "Finish your work before updating." } : {})}
    actions={<>
      <Action disabled={busy} onClick={dismiss}>Later</Action>
      <Action tone="accent" disabled={busy} onClick={() => { void install(); }}>Update now</Action>
    </>} />;
}

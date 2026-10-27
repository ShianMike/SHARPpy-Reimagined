import { Download, HardDrive, MemoryStick, RotateCw, Square } from "lucide-react";
import type { useFrameDownload } from "../../map/useFrameDownload";
import { Action, IconAction } from "../../ui/Controls";
import styles from "./ForecastDownloads.module.css";

/** Download controls live in the existing forecast-hour picker. */
export function ForecastDownloads({ download }: { download: ReturnType<typeof useFrameDownload> }) {
  const failedHours = download.hourStates.some((item) => item.state === "failed");
  const failed = download.failed || failedHours;
  const Icon = download.running ? Square : failed ? RotateCw : Download;
  return <div className={styles.downloads}>
    <div className={styles.row}>
      <p className={styles.counts} aria-label="Forecast cache summary" aria-description={download.message}>
        <HardDrive className="icon-sm" aria-hidden="true" /><span><strong>{download.cacheError ? "—" : download.cached}</strong> saved</span>
        <MemoryStick className="icon-sm" aria-hidden="true" /><span><strong>{download.ready}</strong> ready</span>
      </p>
      <div className={styles.actions}>
        <IconAction icon={RotateCw} size="sm" label="Check Cache" hintSide="top" disabled={download.disabled} onClick={download.refresh} />
        {!download.complete || download.running ? <Action className={styles.primary} tone={download.running ? "quiet" : "accent"} disabled={download.disabled && !download.running} onClick={download.toggle}>
          <Icon className="icon-sm" aria-hidden="true" />{download.running ? "Stop Download" : failed ? "Retry Missing Hours" : "Load Hours"}
        </Action> : null}
      </div>
    </div>
    {!download.cacheError && download.total > 0 ? <progress className={styles.progress} aria-label={download.running ? "Downloaded Forecast Hours" : "Saved Forecast Hours"}
      max={download.total} value={download.running ? download.loaded : download.cached} /> : null}
    {download.cacheError || failed && !download.running ? <p className={styles.error} role="status">{download.cacheError
      ? "Saved-hour cache unavailable. Check cache to try again." : failedHours ? "Select a red hour above to retry its download." : "Some hours are unavailable for this run."}</p> : null}
  </div>;
}

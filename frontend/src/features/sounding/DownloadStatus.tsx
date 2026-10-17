import type { ReactNode } from "react";
import type { SoundingProgress } from "../../types/soundings";
import styles from "./Sounding.module.css";

function transferSize(bytes: number): string {
  if (bytes < 1000) return `${bytes.toFixed(0)} B`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

export function DownloadStatus({ label, progress }: { label: string; progress: SoundingProgress | undefined }): ReactNode {
  const received = progress?.downloadedBytes ?? 0;
  const downloading = progress?.stage === "downloading" && received > 0;
  const phase = progress?.stage === "processing" ? "Processing sounding" : downloading ? "Downloading" : "Waiting for data";
  return (
    <div className={styles.busy} role="status">
      <span>{phase} · {label}</span>
      {received > 0 ? <span className={styles.transfer} aria-live="off">
        <span>{transferSize(received)} received</span>
        {downloading ? <span>{transferSize(progress.bytesPerSecond)}/s</span> : null}
      </span> : null}
    </div>
  );
}

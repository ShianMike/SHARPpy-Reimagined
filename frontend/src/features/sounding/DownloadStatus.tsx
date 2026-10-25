import type { ReactNode } from "react";
import type { SoundingProgress } from "../../types/soundings";
import styles from "./Sounding.module.css";

const STAGES: Record<SoundingProgress["stage"], readonly [string, string]> = {
  waiting: ["Waiting for data", "Locating the requested run and forecast hour. Cached model data is reused when available."],
  downloading: ["Downloading model data", "Receiving weather data from the provider. Downloads can take a minute or more."],
  decoding: ["Decoding model data", "Reading the downloaded or cached data and extracting the vertical profile."],
  calculating: ["Calculating sounding", "Computing parcels, winds and derived sounding parameters."],
  processing: ["Preparing sounding", "Preparing the extracted profile for calculation."],
};

function transferSize(bytes: number): string {
  if (bytes < 1000) return `${bytes.toFixed(0)} B`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

export function DownloadStatus({ label, progress }: { label: string; progress: SoundingProgress | undefined }): ReactNode {
  const received = progress?.downloadedBytes ?? 0;
  const downloading = progress?.stage === "downloading";
  const [phase, detail] = STAGES[progress?.stage ?? "waiting"];
  return (
    <div className={styles.busy} role="status">
      <strong>{phase} · {label}</strong>
      <span>{detail}</span>
      {received > 0 ? <span className={styles.transfer} aria-live="off">
        <span>{transferSize(received)} received</span>
        {downloading ? <span>{transferSize(progress.bytesPerSecond)}/s</span> : null}
      </span> : null}
    </div>
  );
}

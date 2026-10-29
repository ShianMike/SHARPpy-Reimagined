// The open soundings, newest first: select one to view it, close it with X.
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { IconAction } from "../../ui/Controls";
import { useSoundingStore } from "../../stores/soundingStore";
import type { SoundingSummary } from "../../types/soundings";
import { forecastLabel, formatUtc } from "./soundingTime";
import styles from "./Sounding.module.css";

const KIND_LABELS: Readonly<Record<SoundingSummary["kind"], string>> = {
  observed: "Observed",
  model: "Model",
  era5: "ERA5 reanalysis",
  file: "File",
};

/** "Observed, 2026-10-04 12Z, UWyo" or "Model, HRRR F012, valid 2026-10-04 18Z". */
export function soundingMeta(summary: SoundingSummary): string {
  const parts = [KIND_LABELS[summary.kind]];
  if (summary.kind === "model" && summary.model !== null && summary.forecastHour !== null) {
    parts.push(`${summary.model} ${forecastLabel(summary.forecastHour)}`);
    parts.push(`valid ${formatUtc(summary.validTime)}`);
  } else {
    parts.push(formatUtc(summary.validTime));
  }
  if (summary.kind === "observed" && summary.providerName !== null) {
    parts.push(summary.providerName);
  }
  return parts.join(", ");
}

export function OpenSoundings(): ReactNode {
  const open = useSoundingStore((state) => state.open);
  const activeId = useSoundingStore((state) => state.activeId);
  const { setActive, close } = useSoundingStore.getState();
  return (
    <section className={styles.openSection} aria-label="Open soundings">
      <h2 className={styles.sectionTitle}>Open soundings<span className={styles.count}>{open.length}</span></h2>
      {open.length === 0 ? (
        <p className={styles.empty}>Your opened profiles will appear here.</p>
      ) : (
        <ul className={styles.list} data-testid="open-soundings">
          {open.map((summary) => (
            <li key={summary.soundingId} className={styles.item} data-active={summary.soundingId === activeId}>
              <button
                type="button"
                className={styles.itemButton}
                aria-current={summary.soundingId === activeId ? "true" : undefined}
                onClick={() => {
                  setActive(summary.soundingId);
                }}
              >
                <span className={styles.itemTitle} title={summary.title} data-testid={summary.soundingId === activeId ? "sounding-title" : undefined}>{summary.title}</span>
                <span className={styles.itemMeta}>{soundingMeta(summary)}</span>
              </button>
              <IconAction
                icon={X}
                label={`Close ${summary.title}`}
                onClick={() => {
                  close(summary.soundingId);
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

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
  const timeIndices = useSoundingStore((state) => state.timeIndices);
  const { setActive, close } = useSoundingStore.getState();
  return (
    <section className={styles.openSection} aria-label="Open soundings">
      <h2 className={styles.sectionTitle}>Open soundings<span className={styles.count}>{open.length}</span></h2>
      {open.length === 0 ? (
        <p className={styles.empty}>Your opened profiles will appear here.</p>
      ) : (
        <ul className={styles.list} data-testid="open-soundings">
          {open.map((summary) => {
            const { soundingId, model, location, stationId, stationName, forecastHour } = summary;
            const active = soundingId === activeId;
            const validTime = summary.times === undefined ? summary.validTime : summary.times[timeIndices[soundingId] ?? 0] ?? null;
            const frame = summary.kind === "model" && forecastHour !== null ? forecastLabel(forecastHour) : null;

            // Split the standard titles while preserving custom and imported titles.
            let name = summary.title;
            let place: string | null = null;
            if (model !== null && location !== null && name === `${model} ${location}`) {
              name = model;
              place = location;
            } else if (stationId !== null && stationName !== null && name === `${stationId} ${stationName}`) {
              name = stationId;
              place = stationName;
            }

            return (
              <li key={soundingId} className={styles.item} data-active={active}>
                <button
                  type="button"
                  className={styles.itemButton}
                  aria-label={summary.title}
                  aria-description={soundingMeta({ ...summary, validTime })}
                  aria-current={active ? "true" : undefined}
                  onClick={() => { setActive(soundingId); }}
                >
                  <span className={styles.itemLine}>
                    <span className={styles.itemTitle} title={summary.title} data-testid={active ? "sounding-title" : undefined}>{name}</span>
                    {frame === null ? null : <span className={styles.itemNumber}>{frame}</span>}
                  </span>
                  {place === null && validTime === null ? null : (
                    <span className={styles.itemLine}>
                      {place === null ? null : <span className={styles.itemPlace} title={place}>{place}</span>}
                      {validTime === null ? null : (
                        <time className={styles.itemNumber} dateTime={validTime} title={formatUtc(validTime)}>
                          {formatUtc(validTime, { includeYear: false })}
                        </time>
                      )}
                    </span>
                  )}
                </button>
                <IconAction
                  icon={X}
                  size="sm"
                  className={styles.itemClose}
                  label={`Close ${summary.title}`}
                  onClick={() => { close(soundingId); }}
                />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

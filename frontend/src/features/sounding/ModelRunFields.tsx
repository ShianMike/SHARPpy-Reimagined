import { ChevronLeft, ChevronRight } from "lucide-react";
import type { ForecastModel } from "../../types/soundings";
import { IconAction } from "../../ui/Controls";
import { SourceDate, SourceSelect } from "./SourceFields";
import { addHours, forecastLabel, releaseTime } from "./soundingTime";
import styles from "./Sounding.module.css";

/** Choose a UTC day and a scheduled cycle; the provider decides archive coverage. */
export function ModelRunFields({ model, run, onChange }: {
  model: ForecastModel; run: string; onChange: (run: string) => void;
}) {
  const date = run.slice(0, 10);
  const newest = model.recentRuns[0] ?? model.suggestedRun;
  const newestDate = newest.slice(0, 10);
  const scheduled = model.cycles.length > 0;
  const runsOn = (day: string): string[] => model.cycles.map((cycle) => releaseTime(day, cycle))
    .filter((candidate): candidate is string => candidate !== null && candidate <= newest).toSorted();
  const runs = runsOn(date);
  const chooseDate = (day: string): void => {
    const candidates = runsOn(day);
    const next = candidates.find((candidate) => candidate.slice(11) === run.slice(11)) ?? candidates.at(-1);
    if (next !== undefined) onChange(next);
  };

  return <>
    <div className={styles.field}>
      <span>Run date (UTC)</span>
      <div className={styles.runDateRow}>
        <IconAction icon={ChevronLeft} size="sm" label="Previous run date" disabled={!scheduled || date <= "0001-01-01"}
          onClick={() => { chooseDate(addHours(run, -24).slice(0, 10)); }} />
        <SourceDate label="Run date (UTC)" value={date} max={newestDate} disabled={!scheduled} showLabel={false} onChange={chooseDate} />
        <IconAction icon={ChevronRight} size="sm" label="Next run date" disabled={!scheduled || date >= newestDate}
          onClick={() => { chooseDate(addHours(run, 24).slice(0, 10)); }} />
      </div>
    </div>
    <SourceSelect label="Cycle (UTC)" value={run} popup="grid" disabled={!scheduled}
      options={(scheduled ? runs : [run]).map((candidate) => {
        const limit = model.maxForecastHours[new Date(candidate).getUTCHours()] ?? 0;
        return { value: candidate, label: `${candidate.slice(11, 13)}Z`, group: `${date} · UTC`,
          detail: `${candidate > model.suggestedRun ? "Pending · " : candidate === model.suggestedRun ? "Latest · " : ""}${forecastLabel(limit)}` };
      })} onChange={onChange} />
  </>;
}

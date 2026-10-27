import { CalendarDays, ChevronLeft, ChevronRight, GripHorizontal, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { useForecastStore } from "../../stores/forecastStore";
import { Action, IconAction } from "../../ui/Controls";
import type { useFrameDownload } from "../../map/useFrameDownload";
import { ForecastDownloads } from "../timeline/ForecastDownloads";
import type { ForecastContext } from "../forecast/useForecast";
import { addHours, compactUtc, forecastLabel, formatUtc, releaseTime, resolveModelRun, utcDate } from "../sounding/soundingTime";
import { MODEL_REGIONS, modelRegion } from "../sounding/soundingQueries";
import styles from "./ForecastControls.module.css";
import { RunCalendar } from "./RunCalendar";

export type ForecastStep = "model" | "run" | "hour";
export const FORECAST_TITLES = { model: "Forecast model", run: "Model run", hour: "Forecast hour" } as const;

export function ForecastDialog({ context, step, anchor, download, onClose }: {
  context: ForecastContext; step: ForecastStep; anchor: HTMLElement | null; download: ReturnType<typeof useFrameDownload>; onClose: () => void;
}): ReactNode {
  const [query, setQuery] = useState("");
  const [selectedRegion, setSelectedRegion] = useState(() => modelRegion(context.model));
  const regionName = useId();
  const [date, setDate] = useState(context.run.slice(0, 10));
  const [calendarOpen, setCalendarOpen] = useState(false);
  const closeCalendar = (): void => { setCalendarOpen(false); document.getElementById("forecast-run-date")?.focus(); };
  const newestDate = (context.model.recentRuns[0] ?? context.model.suggestedRun).slice(0, 10);
  const day = releaseTime(date, 0);
  const validDate = day !== null && date >= "0001-01-01" && date <= newestDate;
  const searching = query.trim().length > 0;
  const models = context.models.filter((model) => searching
    ? `${model.label} ${model.domain} ${modelRegion(model)}`.toLowerCase().includes(query.trim().toLowerCase())
    : modelRegion(model) === selectedRegion);
  const days = new Map<string, number[]>();
  const hourStates = new Map(download.hourStates.map(({ hour, state }) => [hour, state] as const));
  for (const hour of context.hours) {
    const validDate = utcDate(new Date(addHours(context.run, hour)));
    const hours = days.get(validDate) ?? [];
    hours.push(hour);
    days.set(validDate, hours);
  }
  const done = (): void => { onClose(); anchor?.focus(); };
  const chooseRun = (run: string): void => {
    useForecastStore.getState().setPlaying(false); useForecastStore.getState().setRun(run); done();
  };
  return <>
    <header className={styles.dialogHeader} data-popover-drag><IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move aria-description="Drag to move. Arrow keys move the popup; Home centers it on the screen." /><h2>{FORECAST_TITLES[step]}</h2>{step === "model" ? null : <span className={styles.badge}>{context.model.label}</span>}<IconAction icon={X} size="sm" label="Close forecast setup" onClick={done} /></header>
    {step === "model" ? <>
      <fieldset className={styles.modelRegions} aria-label="Model region">
        {MODEL_REGIONS.map((region, index) => <label key={region} className={styles.regionChoice}>
          <input type="radio" name={regionName} aria-label={region} checked={!searching && selectedRegion === region}
            tabIndex={searching ? (index === 0 ? 0 : -1) : (selectedRegion === region ? 0 : -1)}
            onChange={() => { setSelectedRegion(region); setQuery(""); }} />
          <span>{region}</span><small aria-hidden="true">{context.models.filter((model) => modelRegion(model) === region).length}</small>
        </label>)}
      </fieldset>
      <div className={styles.search}><input data-autofocus type="search" aria-label="Search models" placeholder="Search all models" value={query} onChange={(event) => { setQuery(event.target.value); }} />
        {query.length > 0 ? <Action className={styles.headerAction} aria-label="Clear search" onClick={(event) => { setQuery(""); event.currentTarget.parentElement?.querySelector("input")?.focus(); }}>Clear</Action> : null}
      </div>
      <div className={styles.listHeading}><span>{searching ? `${String(models.length)} results` : `${selectedRegion} models`}</span><span>Grid</span></div>
      <div key={searching ? query : selectedRegion} className={styles.dialogBody}><div className={styles.modelChoices}>
        {MODEL_REGIONS.map((region) => {
          const members = models.filter((model) => modelRegion(model) === region);
          return members.length === 0 ? null : <section key={region} role="group" aria-label={region} className={styles.modelGroup}>
            {searching ? <h3>{region}<span>{members.length}</span></h3> : null}
            {members.map((model) => <button key={model.key} aria-label={`Choose ${model.label}`} type="button" className={styles.modelChoice} aria-pressed={model.key === context.model.key} title={model.notes} onClick={() => {
              if (model.key !== context.model.key) useForecastStore.getState().setModel(model.key);
              done();
            }}><span className={styles.modelIdentity}><strong>{model.label}{model.key === context.model.key ? <small>Selected</small> : null}</strong>{!model.soundingsAvailable ? <small>Maps only</small> : region === "Regional" ? <small>{model.domain}</small> : null}</span>
              <span className={styles.modelResolution}>{model.gridSpacingKm === null ? "—" : `${String(model.gridSpacingKm)} km`}</span></button>)}
          </section>;
        })}
      </div>{models.length === 0 ? <div className={styles.empty}><p>No models found.</p></div> : null}</div>
      <footer className={styles.pickerFooter}><span>Selected <strong>{context.model.label}</strong></span><span>{modelRegion(context.model)}</span></footer>
    </> : step === "run" ? <>
      <div className={styles.dateSection}>
        <label className={styles.name} htmlFor="forecast-run-date">Run date · UTC</label>
        <div className={styles.dateRow}>
          <IconAction icon={ChevronLeft} size="sm" label="Previous run date" disabled={!validDate || date === "0001-01-01"} onClick={() => { if (day !== null) setDate(addHours(day, -24).slice(0, 10)); }} />
          <button data-autofocus id="forecast-run-date" type="button" className={styles.dateButton} aria-label="Run date · UTC" aria-describedby={calendarOpen ? undefined : "archive-availability"} aria-expanded={calendarOpen} onClick={() => { setCalendarOpen(!calendarOpen); }}><CalendarDays className="icon-sm" aria-hidden="true" /><span>{day === null ? date : new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(day))}</span><small>UTC</small></button>
          <IconAction icon={ChevronRight} size="sm" label="Next run date" disabled={!validDate || date >= newestDate} onClick={() => { if (day !== null) setDate(addHours(day, 24).slice(0, 10)); }} />
        </div>
        {calendarOpen ? null : <button className={styles.latestRun} type="button" aria-label="Use latest suggested run" onClick={() => { chooseRun(context.model.suggestedRun); }}><span>Latest suggested</span><strong>{compactUtc(context.model.suggestedRun)}</strong><ChevronRight className="icon-sm" aria-hidden="true" /></button>}
      </div>
      {calendarOpen ? null : <div className={styles.listHeading}><span>Cycle · UTC</span><span>Forecast range</span></div>}
      <div className={styles.dialogBody}>
        {calendarOpen ? <RunCalendar key={date} value={date} max={newestDate} onChange={setDate} onClose={closeCalendar} /> : <>
        {!validDate ? <p className={styles.hint} role="status">Choose a valid date on or before {newestDate}.</p> : null}
        <div className={styles.runChoices}>{context.model.cycles.map((cycle) => {
          const run = releaseTime(date, cycle);
          const disabled = !validDate || run === null || resolveModelRun(context.model, run) !== run;
          const publishing = run !== null && run > context.model.suggestedRun;
          return <button type="button" key={cycle} aria-label={run === null ? `${String(cycle).padStart(2, "0")}Z cycle` : `Run ${compactUtc(run)}`} className={styles.choice} aria-pressed={run === context.run} disabled={disabled}
            title={disabled ? "Cycle has not occurred" : publishing ? "May still be publishing" : formatUtc(run)} onClick={() => { if (run !== null) chooseRun(run); }}>
            <strong>{String(cycle).padStart(2, "0")}Z</strong><small>{disabled ? "—" : publishing ? "Pending" : forecastLabel(context.model.maxForecastHours[cycle] ?? 0)}</small>
          </button>;
        })}</div></>}
      </div>
      {calendarOpen ? null : <footer id="archive-availability" className={styles.pickerFooter}>Choose a cycle to load. Archive coverage varies by model and date; recent cycles may still be publishing.</footer>}
    </> : <>
      <div className={styles.selectionSummary}><span><strong>{forecastLabel(context.hour)}</strong><small>Selected hour</small></span><span><strong>{formatUtc(context.validTime)}</strong><small>Valid · UTC</small></span></div>
      <div className={styles.dialogBody}>{[...days].map(([validDate, hours]) => <section className={styles.hourDay} key={validDate} aria-label={`Valid ${validDate}`}>
        <h3>{validDate}<span>Valid · UTC</span></h3><div className={styles.hourChoices}>
          {hours.map((hour) => {
            const state = hourStates.get(hour) ?? "unknown";
            const status = state === "cached" ? "Downloaded or cached" : state === "loading" ? "Downloading" : state === "failed" ? "Download failed. Select to retry" : state === "missing" ? "Not cached or unavailable for this run" : "Cache status unknown";
            return <button type="button" key={hour} data-autofocus={hour === context.hour ? true : undefined} data-hour-state={state}
              aria-label={`${state === "failed" ? "Retry" : "Select"} forecast hour ${String(hour)}`} aria-description={status}
              title={`${formatUtc(addHours(context.run, hour))} · ${status}`} className={styles.choice} aria-pressed={hour === context.hour} onClick={() => {
                useForecastStore.getState().setPlaying(false); useForecastStore.getState().setHour(hour);
                if (state === "failed") void download.retryHour(hour);
                else done();
              }}><strong>{forecastLabel(hour)}</strong><small>{addHours(context.run, hour).slice(11, 13)}Z</small></button>;
          })}
        </div>
      </section>)}</div>
      <footer className={styles.pickerFooter}>{compactUtc(context.run)} run · {context.hours.length} frames · {forecastLabel(context.hours[0] ?? 0)}–{forecastLabel(context.hours.at(-1) ?? 0)}</footer>
      <ForecastDownloads download={download} />
    </>}
  </>;
}

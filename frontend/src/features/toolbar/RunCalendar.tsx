import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { IconAction } from "../../ui/Controls";
import { addHours, utcDate } from "../sounding/soundingTime";
import styles from "./RunCalendar.module.css";

const MIN_DATE = "0001-01-01";
const MONTHS = Array.from({ length: 12 }, (_, index) => new Intl.DateTimeFormat("en", { month: "long", timeZone: "UTC" }).format(new Date(Date.UTC(2000, index, 1))));
const dateLabel = (date: string): string => new Intl.DateTimeFormat("en", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${date}T00:00:00Z`));

/** Clamp the day before crossing a month, including leap years and January. */
function shiftMonth(date: string, amount: number): string {
  const moment = new Date(`${date}T00:00:00Z`);
  const day = moment.getUTCDate();
  moment.setUTCDate(1);
  moment.setUTCMonth(moment.getUTCMonth() + amount);
  const last = new Date(moment);
  last.setUTCMonth(last.getUTCMonth() + 1, 0);
  moment.setUTCDate(Math.min(day, last.getUTCDate()));
  return utcDate(moment);
}

export function RunCalendar({ value, min = MIN_DATE, max, label = "Run calendar", closeLabel = "Back to cycles", onChange, onClose }: {
  value: string; min?: string; max: string; label?: string; closeLabel?: string; onChange: (date: string) => void; onClose: () => void;
}): ReactNode {
  const [focused, setFocused] = useState(value < min ? min : value > max ? max : value);
  const [monthsOpen, setMonthsOpen] = useState(false);
  const [year, setYear] = useState(value.slice(0, 4));
  const root = useRef<HTMLDivElement>(null);
  const month = focused.slice(0, 7);
  const monthNumber = Number(focused.slice(5, 7)) - 1;
  const first = `${month}-01T00:00:00Z`;
  const offset = new Date(first).getUTCDay();
  const days = Array.from({ length: 42 }, (_, index) => addHours(first, (index - offset) * 24).slice(0, 10));
  const validYear = /^\d{1,4}$/.test(year) && Number(year) >= Number(min.slice(0, 4)) && Number(year) <= Number(max.slice(0, 4));
  const move = (date: string): void => { setFocused(date < min ? min : date > max ? max : date); };
  const choose = (date: string): void => { onChange(date); onClose(); };
  useLayoutEffect(() => { root.current?.querySelector<HTMLElement>(monthsOpen ? "input" : '[data-focused="true"]')?.focus(); }, [focused, monthsOpen]);

  const onDayKey = (event: KeyboardEvent<HTMLButtonElement>): void => {
    const moment = new Date(`${focused}T00:00:00Z`);
    const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -moment.getUTCDay(), End: 6 - moment.getUTCDay() }[event.key];
    if (delta !== undefined) { event.preventDefault(); move(addHours(moment.toISOString(), delta * 24).slice(0, 10)); }
    if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault(); move(shiftMonth(focused, (event.key === "PageUp" ? -1 : 1) * (event.shiftKey ? 12 : 1)));
    }
  };

  return <div ref={root} className={styles.calendar} data-motion="fade" role="region" aria-label={label} onKeyDown={(event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
  }}>
    <div className={styles.heading}>
      {monthsOpen ? <label className={styles.year}>Year<input aria-label="Calendar year" inputMode="numeric" type="number" min={min.slice(0, 4)} max={max.slice(0, 4)} value={year} onChange={(event) => { setYear(event.target.value); }} /></label> : <>
        <IconAction icon={ChevronLeft} size="sm" label="Previous month" disabled={month <= min.slice(0, 7)} onClick={() => { move(shiftMonth(focused, -1)); }} />
        <button type="button" className={styles.monthTitle} aria-label="Choose month and year" onClick={() => { setYear(focused.slice(0, 4)); setMonthsOpen(true); }}>{MONTHS[monthNumber]} {focused.slice(0, 4)}<ChevronDown className="icon-sm" aria-hidden="true" /></button>
        <IconAction icon={ChevronRight} size="sm" label="Next month" disabled={month >= max.slice(0, 7)} onClick={() => { move(shiftMonth(focused, 1)); }} />
      </>}
      {monthsOpen ? <button type="button" className={styles.back} onClick={() => { setMonthsOpen(false); }}>Back to days</button> : null}
    </div>
    {monthsOpen ? <div className={styles.months} data-motion="fade" aria-label="Months">{MONTHS.map((name, index) => {
      const date = `${year.padStart(4, "0")}-${String(index + 1).padStart(2, "0")}-01`;
      return <button type="button" key={name} disabled={!validYear || date > max || date.slice(0, 7) < min.slice(0, 7)} aria-pressed={date.slice(0, 7) === month} onClick={() => { move(date); setMonthsOpen(false); }}>{name.slice(0, 3)}</button>;
    })}</div> : <div role="grid" data-motion="fade" aria-label={`${MONTHS[monthNumber] ?? ""} ${focused.slice(0, 4)}`} className={styles.days}>
      <div role="row" className={styles.weekdays}>{["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((name) => <span role="columnheader" key={name}>{name.slice(0, 2)}</span>)}</div>
      {Array.from({ length: 6 }, (_, week) => <div role="row" key={week}>{days.slice(week * 7, week * 7 + 7).map((date) => <div role="gridcell" key={date} aria-selected={date === value}>
        <button type="button" aria-label={dateLabel(date)} aria-current={date === utcDate(new Date()) ? "date" : undefined} data-autofocus={date === focused ? true : undefined} data-outside={date.slice(0, 7) !== month} data-focused={date === focused} tabIndex={date === focused ? 0 : -1} disabled={date < min || date > max} onKeyDown={onDayKey} onClick={() => { choose(date); }}>{Number(date.slice(8, 10))}</button>
      </div>)}</div>)}
    </div>}
    <footer className={styles.footer}><button type="button" onClick={onClose}>{closeLabel}</button><button type="button" onClick={() => { choose(max); }}>Latest date</button></footer>
  </div>;
}

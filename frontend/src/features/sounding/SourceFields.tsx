import { CalendarDays, ChevronDown, Search, X, type LucideIcon } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { IconAction } from "../../ui/Controls";
import { RunCalendar } from "../toolbar/RunCalendar";
import { releaseTime, utcDate } from "./soundingTime";
import styles from "./Sounding.module.css";
import toolbar from "../toolbar/ForecastControls.module.css";

export function SourceSelect<T extends string | number>({ label, value, options: inputOptions, onChange, disabled = false, compact = false, toolbarIcon: ToolbarIcon, popup, searchLabel }: {
  label: string; value: T; options: readonly { value: T; label: string; shortLabel?: string; detail?: string | undefined; group?: string }[]; onChange: (value: T) => void; disabled?: boolean; compact?: boolean;
  toolbarIcon?: LucideIcon; popup?: "list" | "grid"; searchLabel?: string;
}): ReactNode {
  const id = useId();
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(0);
  const [query, setQuery] = useState("");
  const search = useRef({ text: "", time: 0 });
  const offered = query.trim() === "" ? inputOptions : inputOptions.filter((option) =>
    `${option.label} ${option.detail ?? ""} ${option.group ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const groups = [...new Set(offered.map((option) => option.group))];
  const options = groups.flatMap((group) => offered.filter((option) => option.group === group));
  const selected = Math.max(0, options.findIndex((option) => option.value === value));
  const selectedLabel = inputOptions.find((option) => option.value === value)?.label ?? inputOptions[0]?.label ?? "Choose…";
  const show = (): void => { setQuery(""); setFocused(selected); search.current.text = ""; setOpen(true); };
  const choose = (next: T): void => { onChange(next); setOpen(false); anchor?.focus(); };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (disabled || options.length === 0) return;
    let next = focused;
    if (event.key === "ArrowDown") next = (focused + 1) % options.length;
    else if (event.key === "ArrowUp") next = (focused + options.length - 1) % options.length;
    else if (popup === "grid" && event.key === "ArrowRight") next = (focused + 1) % options.length;
    else if (popup === "grid" && event.key === "ArrowLeft") next = (focused + options.length - 1) % options.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = options.length - 1;
    else if (event.key.length === 1 && event.key !== " " && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = event.timeStamp;
      search.current = { text: (now - search.current.time < 700 ? search.current.text : "") + event.key.toLowerCase(), time: now };
      const match = options.findIndex((option) => option.label.toLowerCase().startsWith(search.current.text));
      if (match !== -1) next = match;
    } else return;
    event.preventDefault();
    setFocused(next);
    list.current?.querySelectorAll<HTMLButtonElement>("[role=option]")[next]?.focus();
  };
  return <div className={ToolbarIcon === undefined ? styles.field : toolbar.select} data-compact={compact}>
    {compact || ToolbarIcon !== undefined ? null : <label htmlFor={id}>{label}</label>}
    <button ref={setAnchor} id={id} type="button" value={value} disabled={disabled} role="combobox" aria-label={label} aria-haspopup={popup === undefined ? "listbox" : "dialog"} aria-expanded={open} aria-controls={open ? `${id}-options` : undefined}
      className={ToolbarIcon === undefined ? styles.fieldButton : toolbar.trigger} onClick={() => { if (open) setOpen(false); else show(); }} onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); show(); }
      }}>{ToolbarIcon === undefined ? <span>{selectedLabel}</span> : <><ToolbarIcon className="icon-sm" aria-hidden="true" /><span className={toolbar.triggerCopy}><span className={toolbar.name}>{label}</span><strong>{selectedLabel}</strong></span></>}<ChevronDown className="icon-sm" aria-hidden="true" /></button>
    <Popover open={open} anchor={anchor} onClose={() => { setOpen(false); }} role={popup === undefined ? "listbox" : "dialog"} label={label} id={`${id}-options`} initialFocus="first" width={popup === undefined ? "anchor" : "content"} side={popup === undefined ? "bottom" : "right"} className={popup === undefined ? styles.sourceMenu : styles.sourcePicker}>
      {popup === undefined ? null : <header className={styles.pickerHeader}>
        <h2>{label}</h2>
        <IconAction icon={X} size="sm" label={`Close ${label.toLowerCase()} picker`} onClick={() => { setOpen(false); anchor?.focus(); }} />
      </header>}
      {searchLabel === undefined ? null : <label className={styles.pickerSearch}>
        <Search className="icon-sm" aria-hidden="true" />
        <input data-autofocus type="search" aria-label={searchLabel} placeholder={searchLabel} value={query} onChange={(event) => { setQuery(event.target.value); setFocused(0); }} onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          const choices = list.current?.querySelectorAll<HTMLButtonElement>("[role=option]");
          choices?.[event.key === "ArrowDown" ? 0 : choices.length - 1]?.focus();
        }} />
      </label>}
      <div ref={list} onKeyDown={onKeyDown} className={popup === undefined ? undefined : styles.pickerBody} role={popup === undefined ? undefined : "listbox"} aria-label={popup === undefined ? undefined : label}>
        {groups.map((group) => <div key={group ?? ""} role={group === undefined ? undefined : "group"} aria-label={group} className={group === undefined ? undefined : styles.sourceGroup}>
          {group === undefined ? null : <div className={styles.sourceGroupHeading} aria-hidden="true">{group}</div>}
          <div className={popup === "grid" ? styles.pickerGrid : undefined}>
            {options.map((option, index) => option.group !== group ? null : <button key={option.value} type="button" value={option.value} disabled={disabled} role="option" aria-label={option.label} aria-description={option.detail} aria-selected={option.value === value} tabIndex={focused === index ? 0 : -1}
              data-autofocus={searchLabel === undefined && selected === index ? true : undefined} className={styles.sourceOption} onFocus={() => { setFocused(index); }} onClick={() => { choose(option.value); }}>
              <span>{option.shortLabel ?? option.label}{option.detail === undefined ? null : <small>{option.detail}</small>}</span>
            </button>)}
          </div>
        </div>)}
        {options.length === 0 ? <p role="status" className={styles.pickerEmpty}>No matches.</p> : null}
      </div>
    </Popover>
  </div>;
}

export function SourceDate({ label = "Date (UTC)", value, min = "0001-01-01", max = utcDate(new Date()), onChange }: {
  label?: string; value: string; min?: string; max?: string; onChange: (date: string) => void;
}): ReactNode {
  const id = useId();
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const time = releaseTime(value, 0);
  const close = (): void => { setOpen(false); anchor?.focus(); };
  return <div className={styles.field}>
    <label htmlFor={id}>{label}</label>
    <button ref={setAnchor} id={id} type="button" value={value} aria-label={label} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? `${id}-calendar` : undefined}
      className={styles.fieldButton} onClick={() => { setOpen(!open); }}>
      <CalendarDays className="icon-sm" aria-hidden="true" /><span>{time === null ? "Choose a date" : new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(time))}</span><ChevronDown className="icon-sm" aria-hidden="true" />
    </button>
    <Popover open={open} anchor={anchor} onClose={() => { setOpen(false); }} role="dialog" label={label} id={`${id}-calendar`} initialFocus="first" side="right" className={styles.sourceCalendar}>
      <RunCalendar value={time === null ? max : value} min={min} max={max} label={label} closeLabel="Done" onChange={onChange} onClose={close} />
    </Popover>
  </div>;
}

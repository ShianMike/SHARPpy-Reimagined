import { CalendarDays, ChevronDown, type LucideIcon } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { RunCalendar } from "../toolbar/RunCalendar";
import { releaseTime, utcDate } from "./soundingTime";
import styles from "./Sounding.module.css";
import toolbar from "../toolbar/ForecastControls.module.css";

export function SourceSelect<T extends string | number>({ label, value, options: inputOptions, onChange, disabled = false, compact = false, toolbarIcon: ToolbarIcon }: {
  label: string; value: T; options: readonly { value: T; label: string; detail?: string; group?: string }[]; onChange: (value: T) => void; disabled?: boolean; compact?: boolean;
  toolbarIcon?: LucideIcon;
}): ReactNode {
  const id = useId();
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(0);
  const search = useRef({ text: "", time: 0 });
  const groups = [...new Set(inputOptions.map((option) => option.group))];
  const options = groups.flatMap((group) => inputOptions.filter((option) => option.group === group));
  const selected = Math.max(0, options.findIndex((option) => option.value === value));
  const show = (): void => { setFocused(selected); search.current.text = ""; setOpen(true); };
  const choose = (next: T): void => { onChange(next); setOpen(false); anchor?.focus(); };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (disabled || options.length === 0) return;
    let next = focused;
    if (event.key === "ArrowDown") next = (focused + 1) % options.length;
    else if (event.key === "ArrowUp") next = (focused + options.length - 1) % options.length;
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
    <button ref={setAnchor} id={id} type="button" value={value} disabled={disabled} role="combobox" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? `${id}-options` : undefined}
      className={ToolbarIcon === undefined ? styles.fieldButton : toolbar.trigger} onClick={() => { if (open) setOpen(false); else show(); }} onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); show(); }
      }}>{ToolbarIcon === undefined ? <span>{options[selected]?.label ?? "Choose…"}</span> : <><ToolbarIcon className="icon-sm" aria-hidden="true" /><span className={toolbar.triggerCopy}><span className={toolbar.name}>{label}</span><strong>{options[selected]?.label ?? "Choose…"}</strong></span></>}<ChevronDown className="icon-sm" aria-hidden="true" /></button>
    <Popover open={open} anchor={anchor} onClose={() => { setOpen(false); }} role="listbox" label={label} id={`${id}-options`} initialFocus="first" width="anchor" className={styles.sourceMenu}>
      <div ref={list} onKeyDown={onKeyDown}>
        {groups.map((group) => <div key={group ?? ""} role={group === undefined ? undefined : "group"} aria-label={group} className={group === undefined ? undefined : styles.sourceGroup}>
          {group === undefined ? null : <div className={styles.sourceGroupHeading} aria-hidden="true">{group}</div>}
          {options.map((option, index) => option.group !== group ? null : <button key={option.value} type="button" value={option.value} disabled={disabled} role="option" aria-label={option.label} aria-description={option.detail} aria-selected={option.value === value} tabIndex={focused === index ? 0 : -1}
            data-autofocus={selected === index ? true : undefined} className={styles.sourceOption} onFocus={() => { setFocused(index); }} onClick={() => { choose(option.value); }}>
            <span>{option.label}{option.detail === undefined ? null : <small>{option.detail}</small>}</span>
          </button>)}
        </div>)}
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

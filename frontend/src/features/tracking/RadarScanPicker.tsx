import { ChevronDown, ChevronLeft, ChevronRight, Clock3, GripHorizontal, Radio, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { cx } from "../../lib/cx";
import { usePanelRadar, useRadarFrame, useRadarSiteFrame } from "../../map/mapQueries";
import { LIVE_HISTORY_MINUTES, LIVE_MINUTE, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { panelLayers, panelWeather, useMapStore } from "../../stores/mapStore";
import { Action, IconAction, RangeControl } from "../../ui/Controls";
import controls from "../toolbar/ForecastControls.module.css";
import styles from "./RadarScanPicker.module.css";
import hints from "../../ui/HoverHint.module.css";

const DAY_MS = 24 * 60 * LIVE_MINUTE;
const TIME_STEP = 5 * LIVE_MINUTE;
const hhmm = (time: number): string => liveTimestamp(time).slice(11, 16);
const utcDateTime = (time: number): string => `${liveTimestamp(time).slice(0, 10)} ${hhmm(time)} UTC`;

function resolvePastHourUtcTime(raw: string, now: number): { time: number } | { error: string } {
  const earliest = now - LIVE_HISTORY_MINUTES * LIVE_MINUTE;
  const trimmed = raw.trim().replace(/\s*(?:utc|z)$/i, "");
  if (/^(?:live|now)$/i.test(trimmed)) return { time: now };
  const match = /^(\d{1,2}):(\d{2})$/.exec(trimmed) ?? /^(\d{2})(\d{2})$/.exec(trimmed);
  if (match === null) {
    return { error: `Enter HH:MM UTC between ${hhmm(earliest)} and ${hhmm(now)}.` };
  }
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) {
    return { error: `Enter HH:MM UTC between ${hhmm(earliest)} and ${hhmm(now)}.` };
  }
  const nowDate = new Date(now);
  const sameDay = Date.UTC(nowDate.getUTCFullYear(), nowDate.getUTCMonth(), nowDate.getUTCDate(), hour, minute);
  const target = [sameDay - DAY_MS, sameDay, sameDay + DAY_MS].find((candidate) => candidate >= earliest && candidate <= now);
  if (target === undefined) {
    return { error: `Time must be between ${hhmm(earliest)} and ${hhmm(now)} UTC.` };
  }
  return { time: target };
}

export function RadarScanPicker({ onSelect }: { onSelect: (time: number) => void }): ReactNode {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timeInputId = useId();
  const windowId = useId();
  const errorId = useId();
  const { now, selectedTime } = useLiveTrackingStore();
  const time = selectedTime ?? now;
  const scope = useMapStore((state) => panelWeather(state).radarScope);
  const visible = useMapStore((state) => panelLayers(state, state.activePanel).radar.visible);
  const { site, product, tilt } = usePanelRadar();
  const mosaic = useRadarFrame(product, false, liveTimestamp(time));
  const antenna = useRadarSiteFrame(product, site, false, liveTimestamp(time), tilt);
  const radar = scope === "site" ? antenna : mosaic;
  const scan = radar.data?.validTime;
  const earliest = now - LIVE_HISTORY_MINUTES * LIVE_MINUTE;
  const minutesAgo = Math.round((now - time) / LIVE_MINUTE);
  const inputValue = draft ?? hhmm(time);
  const close = (): void => { setOpen(false); setDraft(null); setError(null); anchor?.focus(); };
  const selectMinute = (target: number, shouldClose = false): void => {
    setDraft(null);
    setError(null);
    onSelect(Math.max(earliest, Math.min(now, target)));
    if (shouldClose) close();
  };
  const submitTime = (): void => {
    const resolved = resolvePastHourUtcTime(inputValue, now);
    if ("error" in resolved) {
      setError(resolved.error);
      return;
    }
    selectMinute(resolved.time, true);
  };
  return <>
    <button type="button" className={cx(controls.trigger, hints.target)} aria-label="Choose radar time" aria-haspopup="dialog" aria-expanded={open}
      onClick={(event) => { setAnchor(event.currentTarget); setDraft(null); setError(null); setOpen(!open); }}>
      <Clock3 className="icon-sm" aria-hidden="true" /><span className={controls.triggerCopy}><span className={controls.name}>Radar time</span>
        <strong>{selectedTime === null ? "Live" : `${hhmm(time)} UTC`}</strong></span><ChevronDown className="icon-sm" aria-hidden="true" />
      <span className={hints.hint} data-side="bottom" aria-hidden="true">Follow live or choose a UTC time in the past hour.</span>
    </button>
    <Popover open={open} anchor={anchor} movable role="dialog" label="Radar time" initialFocus="first" width="content"
      className={controls.picker} onClose={() => { setOpen(false); setDraft(null); setError(null); }}>
      <header className={controls.dialogHeader} data-popover-drag>
        <IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move aria-description="Drag to move. Arrow keys move the popup; Home centers it on the screen." />
        <h2>Radar time</h2><span className={controls.badge}>Past hour</span><IconAction icon={X} size="sm" label="Close radar time" onClick={close} />
      </header>
      <div className={controls.selectionSummary}>
        <span><strong>{selectedTime === null ? "Live" : `−${String(minutesAgo)}m`}</strong><small>{selectedTime === null ? "Following live" : "Selected offset"}</small></span>
        <span><strong>{utcDateTime(time)}</strong><small>Requested · UTC</small></span>
      </div>
      <div className={cx(controls.dialogBody, styles.body)}>
        <p className={styles.scanTime} role="status">{!visible ? "Radar layer hidden" : scan == null ? radar.isFetching ? "Loading radar scan…" : "No radar scan loaded" : `Radar scan · ${liveTimestamp(Date.parse(scan)).replace("T", " ").replace("Z", " UTC")}${radar.isFetching ? " · updating" : ""}`}</p>
        <form className={styles.timeForm} onSubmit={(event) => { event.preventDefault(); submitTime(); }} noValidate>
          <label className={styles.timeLabel} htmlFor={timeInputId}>UTC time (HH:MM)</label>
          <div className={styles.timeRow}>
            <IconAction icon={ChevronLeft} size="sm" label="Previous 5 minutes" disabled={time <= earliest} onClick={() => { selectMinute(time - TIME_STEP); }} />
            <div className={styles.timeField} data-invalid={error !== null}>
              <input id={timeInputId} data-autofocus type="text" inputMode="numeric" placeholder="HH:MM"
                aria-label="UTC time" aria-invalid={error !== null} aria-describedby={error !== null ? errorId : windowId}
                value={inputValue} onChange={(event) => {
                  setDraft(event.currentTarget.value);
                  if (error !== null) setError(null);
                }} />
              <span className={styles.utcTag} aria-hidden="true">UTC</span>
            </div>
            <IconAction icon={ChevronRight} size="sm" label="Next 5 minutes" disabled={time >= now} onClick={() => { selectMinute(time + TIME_STEP); }} />
            <Action type="submit" tone="accent" aria-label="Apply UTC time">Set</Action>
          </div>
          {error === null ? null : <p id={errorId} className={styles.error} role="alert">{error}</p>}
        </form>
        <RangeControl label="Past hour minute" visibleLabel="Past hour"
          value={-minutesAgo} min={-LIVE_HISTORY_MINUTES} max={0} step={1}
          formatValue={(offset) => offset === 0 ? "Live" : `${hhmm(now + offset * LIVE_MINUTE)} UTC (${String(offset)}m)`}
          onChange={(offset) => { selectMinute(now + offset * LIVE_MINUTE); }} />
      </div>
      <footer className={cx(controls.pickerFooter, styles.footer)}>
        <span id={windowId}>{selectedTime === null ? `Following live · ${hhmm(earliest)}–${hhmm(now)} UTC` : `Window ${hhmm(earliest)}–${hhmm(now)} UTC`}</span>
        <Action tone={selectedTime === null ? "quiet" : "accent"} aria-label="Return to Live" disabled={selectedTime === null}
          onClick={() => { selectMinute(now, true); }}>
          <Radio className="icon-sm" aria-hidden="true" />Live
        </Action>
      </footer>
    </Popover>
  </>;
}

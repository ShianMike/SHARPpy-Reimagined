import { Check, ChevronDown, Clock3, Download, GripHorizontal, RefreshCw, Square, TriangleAlert, X } from "lucide-react";
import { useState } from "react";
import { Popover } from "../../components/Popover";
import { cx } from "../../lib/cx";
import { LIVE_MINUTE, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { Action, IconAction } from "../../ui/Controls";
import { formatUtc } from "../sounding/soundingTime";
import { RADAR_FRAME_LABELS, type RadarPlaybackFrame } from "./radarPlayback";
import type { useLiveHistory } from "./useLiveHistory";
import controls from "../toolbar/ForecastControls.module.css";
import styles from "./RadarScanPicker.module.css";
import hints from "../../ui/HoverHint.module.css";

export function RadarScanPicker({ history, status, fetching, onSelect }: {
  history: ReturnType<typeof useLiveHistory>; status: string; fetching: boolean; onSelect: (time: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const { now, selectedTime } = useLiveTrackingStore();
  const time = selectedTime ?? now;
  const { progress, frames } = history;
  const cached = frames.filter((frame) => frame.state === "available" || frame.state === "delayed").length;
  const ready = !progress.running && !progress.stopped && progress.total > 0 && progress.done === progress.total && progress.failed === 0 && cached === frames.length;
  const DownloadIcon = progress.running ? Square : ready ? Check : progress.failed > 0 ? TriangleAlert : Download;
  const days = new Map<string, RadarPlaybackFrame[]>();
  for (const frame of frames) {
    const date = liveTimestamp(frame.time).slice(0, 10);
    days.set(date, [...(days.get(date) ?? []), frame]);
  }
  const close = (): void => { setOpen(false); anchor?.focus(); };
  return <>
    <button type="button" className={cx(controls.trigger, hints.target)} aria-label="Choose radar scan" aria-haspopup="dialog" aria-expanded={open}
      onClick={(event) => { setAnchor(event.currentTarget); setOpen(!open); }}>
      <Clock3 className="icon-sm" aria-hidden="true" /><span className={controls.triggerCopy}><span className={controls.name}>Radar scans</span>
        <strong>{selectedTime === null ? "Live" : `${liveTimestamp(time).slice(11, 16)} UTC`}</strong></span><ChevronDown className="icon-sm" aria-hidden="true" />
      <span className={hints.hint} data-side="bottom" aria-hidden="true">Choose a scan, check availability, or load the past hour.</span>
    </button>
    <Popover open={open} anchor={anchor} movable role="dialog" label="Radar scans" initialFocus="first" width="content"
      className={cx(controls.picker, controls.hourPicker)} onClose={() => { setOpen(false); }}>
      <header className={controls.dialogHeader} data-popover-drag>
        <IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move aria-description="Drag to move. Arrow keys move the popup; Home centers it on the screen." />
        <h2>Radar scans</h2><span className={controls.badge}>Past hour</span><IconAction icon={X} size="sm" label="Close radar scans" onClick={close} />
      </header>
      <div className={controls.selectionSummary}><span><strong>{selectedTime === null ? "Live" : `−${String(Math.round((now - time) / LIVE_MINUTE))}m`}</strong><small>Selected frame</small></span>
        <span><strong>{formatUtc(liveTimestamp(time))}</strong><small>Viewing · UTC</small></span></div>
      <div className={controls.dialogBody}>
        {frames.length === 0 ? <p className={controls.hint}>Enable radar in a panel to see its scan availability.</p> : <div role="group" aria-label="Radar scan availability">
          {[...days].map(([date, scans]) => <section className={controls.hourDay} key={date} aria-label={`Scans ${date}`}>
            <h3>{date}<span>Scan request · UTC</span></h3><div className={controls.hourChoices}>
              {scans.map((frame) => <button key={frame.time} type="button" className={cx(controls.choice, styles.scan)} data-scan-state={frame.state}
                aria-label={`${liveTimestamp(frame.time).slice(11, 16)} UTC · ${RADAR_FRAME_LABELS[frame.state]}`} aria-description={frame.detail} title={frame.detail}
                aria-pressed={frame.time === time} onClick={() => { onSelect(frame.time); if (frame.state !== "failed" && frame.state !== "missing") close(); }}>
                <strong>{liveTimestamp(frame.time).slice(11, 16)}</strong><small>{frame.state === "failed" ? "Failed" : RADAR_FRAME_LABELS[frame.state]}</small>
              </button>)}
            </div>
          </section>)}
        </div>}
      </div>
      <footer className={controls.pickerFooter}>
        <p>{status}</p>
        <div className={styles.downloads}>
          <span>{cached} / {frames.length} radar frames cached</span>
          <IconAction icon={RefreshCw} size="sm" label="Refresh live layers" disabled={fetching || progress.running} onClick={() => { void history.refresh(); }} />
          <Action tone="accent" aria-label={progress.running ? "Stop caching" : "Cache past hour"} aria-description="Load the past hour for all panels at full resolution. Kept until the app closes."
            onClick={() => { if (progress.running) history.stop(); else void history.cacheHour(); }}>
            <DownloadIcon className="icon-sm" aria-hidden="true" />{progress.running ? "Loading" : ready ? "Ready" : progress.stopped ? "Resume" : progress.failed > 0 ? "Retry" : "Load hour"}
          </Action>
        </div>
        {progress.running ? <progress className={styles.progress} aria-label="Caching live history" value={progress.done} max={Math.max(1, progress.total)} /> : null}
      </footer>
    </Popover>
  </>;
}

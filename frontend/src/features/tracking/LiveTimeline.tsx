import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Radio, Repeat, StepBack, StepForward } from "lucide-react";
import { useEffect, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "../../lib/cx";
import { LIVE_HISTORY_MINUTES, LIVE_MINUTE, liveHistoryTimes, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { PLAYBACK_SPEEDS, useUiStore } from "../../stores/uiStore";
import { Action, IconAction } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import { formatUtc } from "../sounding/soundingTime";
import { isLiveQuery, trimLiveCache } from "./liveRequests";
import { useLiveHistory } from "./useLiveHistory";
import { RadarScanPicker } from "./RadarScanPicker";
import styles from "../timeline/TimelineRegion.module.css";
import hints from "../../ui/HoverHint.module.css";
import { MapInteractionControls } from "../timeline/MapInteractionControls";

export function LiveTimeline({ className, scanTarget }: { className?: string | undefined; scanTarget?: HTMLDivElement | null }): ReactNode {
  const { now, selectedTime, playing, selectTime, setPlaying } = useLiveTrackingStore();
  const { speed, loop } = useUiStore();
  const time = selectedTime ?? now;
  const minutes = Math.round((now - time) / LIVE_MINUTE);
  const times = liveHistoryTimes(now);
  const previous = times.findLast((step) => step < time), next = times.find((step) => step > time);
  const queryClient = useQueryClient();
  const fetching = useIsFetching({ predicate: (query) => isLiveQuery(query.queryKey) && query.getObserversCount() > 0 });
  const failed = queryClient.getQueryCache().findAll({ predicate: (query) => isLiveQuery(query.queryKey) && query.getObserversCount() > 0 && query.state.status === "error" }).length;
  const history = useLiveHistory();
  const { progress } = history;
  useEffect(() => {
    useLiveTrackingStore.getState().tick();
    const timer = setInterval(() => { useLiveTrackingStore.getState().tick(); }, LIVE_MINUTE);
    return () => { clearInterval(timer); };
  }, []);
  useEffect(() => { if (fetching === 0) trimLiveCache(queryClient); }, [fetching, queryClient]);
  const status = progress.running ? `Caching ${String(progress.done)} / ${String(progress.total)}`
    : progress.stopped ? "Caching stopped"
    : failed > 0 ? `${String(failed)} layers unavailable · see Layers`
    : fetching > 0 || history.waiting ? "Fetching live data…"
    : progress.total > 0 ? `${String(progress.done - progress.failed)} cached${progress.failed > 0 ? ` · ${String(progress.failed)} unavailable` : ""}`
    : "Past 60 minutes · session cache cleared on exit";
  const choose = (time: number | null): void => { setPlaying(false); selectTime(time); };
  const toggle = (): void => {
    if (!playing && (selectedTime === null || selectedTime >= now)) selectTime(times[0] ?? now);
    setPlaying(!playing);
  };
  return <Surface as="section" className={cx(styles.timeline, className)} aria-label="Live Tracking timeline" data-testid="live-timeline">
    {scanTarget == null ? null : createPortal(<RadarScanPicker history={history} status={status} fetching={fetching > 0}
      onSelect={(frame) => { choose(frame === now ? null : frame); void history.loadFrame(frame); }} />, scanTarget)}
    <MapInteractionControls />
    <div className={styles.transport} role="group" aria-label="Playback">
      <IconAction icon={StepBack} size="sm" label="Previous frame" hintSide="top" disabled={previous === undefined} onClick={() => { if (previous !== undefined) choose(previous); }} />
      <IconAction className={styles.play} icon={playing ? Pause : Play} label={playing ? "Pause" : "Play"} hintSide="top" onClick={toggle} />
      <IconAction icon={StepForward} size="sm" label="Next frame" hintSide="top" disabled={next === undefined} onClick={() => { if (next !== undefined) choose(next); }} />
    </div>
    <strong className={styles.selection}><span className={styles.model}>Live Tracking</span><span className={styles.hour}>{selectedTime === null ? "LIVE" : `−${String(minutes)}m`}</span></strong>
    <div className={styles.scrubber}>
      <input id="live-history-time" type="range" aria-label="Live history" min={-LIVE_HISTORY_MINUTES} max={0} step={1} value={-minutes}
        aria-valuetext={`${minutes === 0 ? "Live" : `${String(minutes)} minutes ago`} · ${formatUtc(liveTimestamp(time)).slice(5)} UTC`}
        style={{ "--played": `${String((LIVE_HISTORY_MINUTES - minutes) / LIVE_HISTORY_MINUTES * 100)}%` } as CSSProperties}
        onChange={(event) => { const offset = Number(event.currentTarget.value); choose(offset === 0 ? null : now + offset * LIVE_MINUTE); }} />
      <span className={styles.endHour}>Now</span>
    </div>
    <output className={cx(styles.valid, hints.target)} tabIndex={0} aria-label="Live viewing time" aria-description={status} aria-busy={fetching > 0 || history.waiting}>
      <span>{fetching > 0 || history.waiting ? "Loading" : "Viewing"}</span><strong>{formatUtc(liveTimestamp(time)).slice(5)} UTC</strong>
      <span className={hints.hint} data-side="top" aria-hidden="true">{status}</span>
    </output>
    <div className={cx(styles.options, styles.liveOptions)}>
      <button type="button" className={cx(styles.speed, hints.target)} aria-label={`Playback speed ${String(speed)} times`} onClick={() => {
        const current = PLAYBACK_SPEEDS.findIndex((item) => item === speed);
        useUiStore.getState().setSpeed(PLAYBACK_SPEEDS[(current + 1) % PLAYBACK_SPEEDS.length] ?? 1);
      }}>{speed}×<span className={hints.hint} data-side="top" aria-hidden="true">Change playback speed · {speed}×</span></button>
      <IconAction icon={Repeat} size="sm" label="Loop live animation" hintSide="top" aria-pressed={loop} onClick={() => { useUiStore.getState().setLoop(!loop); }} />
      <Action className={cx(styles.option, hints.target)} aria-label="Live" aria-pressed={selectedTime === null} onClick={() => { useLiveTrackingStore.getState().tick(); choose(null); }}>
        <Radio className="icon" aria-hidden="true" />Live<span className={hints.hint} data-side="top" aria-hidden="true">{selectedTime === null ? "Following current live data" : "Return to current live data"}</span>
      </Action>
    </div>
    <span className={styles.announcement} role="status" aria-label="Live data status">{status}</span>
  </Surface>;
}

import { useIsFetching } from "@tanstack/react-query";
import { Blend, Pause, Play, Repeat, StepBack, StepForward, Zap } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { Action, IconAction } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import { cx } from "../../lib/cx";
import { hourAfter } from "../../map/usePlayback";
import { useForecastStore } from "../../stores/forecastStore";
import { PLAYBACK_SPEEDS, useUiStore } from "../../stores/uiStore";
import { useForecast } from "../forecast/useForecast";
import { compactUtc, forecastLabel } from "../sounding/soundingTime";
import { FORECAST_RANGE_ID } from "../toolbar/ForecastControls";
import styles from "./TimelineRegion.module.css";
import hints from "../../ui/HoverHint.module.css";

export { FORECAST_RANGE_ID } from "../toolbar/ForecastControls";
export interface TimelineRegionProps { className?: string | undefined; }
export function TimelineRegion({ className }: TimelineRegionProps): ReactNode {
  const forecast = useForecast();
  const playing = useForecastStore((state) => state.playing);
  const speed = useUiStore((state) => state.speed);
  const loop = useUiStore((state) => state.loop);
  const smooth = useUiStore((state) => state.smoothPlayback);
  const context = forecast.status === "ready" ? forecast.context : null;
  const loading = useIsFetching({ predicate: ({ queryKey: key }) =>
    (key[0] === "fields" || key[0] === "contours") && key[2] === context?.run && key[3] === context?.hour && key[4] === context?.model.key,
  }) > 0;
  const hours = context?.hours ?? [];
  const index = Math.max(0, hours.indexOf(context?.hour ?? 0));
  const last = Math.max(0, hours.length - 1);
  const selectionHint = context === null ? "Waiting for forecast" : `${context.model.label} · ${compactUtc(context.run)} run · ${String(hours.length)} frames`;
  const validHint = loading ? "Loading frame…" : "Valid time in UTC";
  const choose = (hour: number): void => { useForecastStore.getState().setPlaying(false); useForecastStore.getState().setHour(hour); };
  const step = (direction: 1 | -1): void => {
    if (context === null) return;
    const next = hourAfter(hours, context.hour, direction, false);
    if (next !== null) choose(next);
  };
  return <Surface as="section" data-motion="fade" aria-label="Forecast timeline" className={cx(styles.timeline, className)} data-testid="timeline">
    <div className={styles.transport} role="group" aria-label="Playback">
      <IconAction icon={StepBack} size="sm" label="Previous frame" hintSide="top" disabled={context === null || index === 0} onClick={() => { step(-1); }} />
      <IconAction className={styles.play} icon={playing ? Pause : Play} label={playing ? "Pause" : "Play"} hintSide="top" disabled={hours.length < 2} onClick={() => { useForecastStore.getState().setPlaying(!playing); }} />
      <IconAction icon={StepForward} size="sm" label="Next frame" hintSide="top" disabled={context === null || index === last} onClick={() => { step(1); }} />
    </div>
    <strong className={cx(styles.selection, hints.target)} tabIndex={0} aria-description={selectionHint}>
      <span className={styles.model}>{context?.model.label ?? "Forecast"}</span><span className={styles.hour}>{context === null ? "—" : forecastLabel(context.hour)}</span>
      <span className={hints.hint} data-side="top" aria-hidden="true">{selectionHint}</span>
    </strong>
    <div className={cx(styles.scrubber, hints.target)}>
      <input id={FORECAST_RANGE_ID} type="range" aria-label="Forecast hour" min={0} max={last} step={1} value={index} disabled={hours.length < 2}
        aria-valuetext={context === null ? "Forecast unavailable" : `${forecastLabel(context.hour)} · valid ${compactUtc(context.validTime)} UTC`}
        style={{ "--played": `${String(last === 0 ? 0 : index / last * 100)}%` } as CSSProperties}
        onChange={(event) => { const hour = hours[Number(event.currentTarget.value)]; if (hour !== undefined) choose(hour); }} />
      {context !== null ? <span className={styles.endHour}>{forecastLabel(hours[last] ?? 0)}</span> : <span className={styles.waiting}>{forecast.status === "error" ? "Forecast unavailable" : forecast.status === "empty" ? "No forecast models" : "Loading forecast…"}</span>}
      <span className={hints.hint} data-side="top" aria-hidden="true">{context === null ? "Forecast unavailable" : `${forecastLabel(context.hour)} · Valid ${compactUtc(context.validTime)} UTC`}</span>
    </div>
    <output className={cx(styles.valid, hints.target)} tabIndex={0} aria-label="Valid time" aria-description={validHint} aria-busy={loading}>
      <span>{loading ? "Loading" : "Valid"}</span><strong>{context === null ? "—" : `${compactUtc(context.validTime)} UTC`}</strong>
      <span className={hints.hint} data-side="top" aria-hidden="true">{validHint}</span>
    </output>
    <div className={styles.options}>
      <button type="button" className={cx(styles.speed, hints.target)} aria-label={`Playback speed ${String(speed)} times`} onClick={() => {
        const current = PLAYBACK_SPEEDS.findIndex((item) => item === speed);
        useUiStore.getState().setSpeed(PLAYBACK_SPEEDS[(current + 1) % PLAYBACK_SPEEDS.length] ?? 1);
      }}>{speed}×<span className={hints.hint} data-side="top" aria-hidden="true">Change playback speed · {speed}×</span></button>
      <IconAction icon={Repeat} size="sm" label="Loop Forecast Animation" hintSide="top" aria-pressed={loop} onClick={() => { useUiStore.getState().setLoop(!loop); }} />
      <Action className={cx(styles.option, styles.mode, hints.target)} aria-label="Smooth Playback" aria-pressed={smooth}
        onClick={() => { useUiStore.getState().setSmoothPlayback(!smooth); }}>
        {smooth ? <Blend className="icon" aria-hidden="true" /> : <Zap className="icon" aria-hidden="true" />}
        <span className={hints.hint} data-side="top" aria-hidden="true">{smooth ? "Smooth · Blend Between Frames" : "Fast · Switch Frames Instantly"}</span>
      </Action>
    </div>
  </Surface>;
}

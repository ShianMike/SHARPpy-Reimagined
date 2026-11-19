import type { BackendClient } from "../../lib/backend/client";
import type { ForecastModel, ImageMode, RenderChoice, SoundingColorStyle, SoundingSummary } from "../../types/soundings";
import type { ViewRequest } from "../../types/soundingView";
import type { AnimationFrame } from "../animation/encodeAnimation";
import { useSoundingStore } from "../../stores/soundingStore";
import { compactUtc, forecastLabel } from "./soundingTime";
import { INITIAL_VIEW } from "./viewHistory";

export const SOUNDING_ANIMATION_FRAME_LIMIT = 12;

export interface SoundingFrameChoice { value: number; label: string; timeLabel?: string }
export interface SoundingAnimationProgress { phase: "loading" | "scaling" | "rendering" | "encoding"; done: number; total: number }
export interface PreparedSoundings { frames: AnimationFrame[]; width: number; height: number }
export interface SoundingAnimationOptions {
  summary: SoundingSummary; model: ForecastModel | undefined; selected: readonly SoundingFrameChoice[];
  choice: RenderChoice; quality: ImageMode; colorStyle: SoundingColorStyle; view: ViewRequest;
}

export function soundingFrameChoices(summary: SoundingSummary, model: ForecastModel | undefined): SoundingFrameChoice[] {
  if (summary.kind === "model") {
    if (model === undefined || summary.runTime === null || summary.box !== undefined) return [];
    const limit = model.maxForecastHours[new Date(summary.runTime).getUTCHours()] ?? 0;
    return model.forecastHours.filter((hour) => hour <= limit).map((value) => ({ value, label: forecastLabel(value) }));
  }
  return summary.timeCount < 2 ? [] : Array.from({ length: summary.timeCount }, (_, value) => {
    const time = summary.times?.[value];
    const profile = String(value + 1);
    if (time == null) return { value, label: `Profile ${profile}` };
    const timeLabel = `${compactUtc(time)} UTC`;
    return { value, label: `${profile} · ${timeLabel}`, timeLabel };
  });
}

/** Source profiles are prepared first; both plots then use one fixed extent. */
export async function prepareSoundingAnimation(options: SoundingAnimationOptions, client: BackendClient,
  signal: AbortSignal, progress: (state: SoundingAnimationProgress) => void): Promise<PreparedSoundings> {
  signal.throwIfAborted();
  if (options.selected.length < 2 || options.selected.length > SOUNDING_ANIMATION_FRAME_LIMIT) {
    throw new Error(`Choose between 2 and ${String(SOUNDING_ANIMATION_FRAME_LIMIT)} sounding frames.`);
  }
  const { summary } = options;
  const available = soundingFrameChoices(summary, options.model);
  if (!options.selected.every((item) => available.some((choice) => choice.value === item.value)))
    throw new Error("A selected sounding hour is unavailable.");
  const loaded: { summary: SoundingSummary; view: ViewRequest; label: string }[] = [];
  const report = (phase: SoundingAnimationProgress["phase"], done: number): void => { progress({ phase, done, total: options.selected.length }); };
  report("loading", 0);
  const open = useSoundingStore.getState().open;
  // Confirm retention before adding animation sources; this protects visible
  // profiles without requesting their scientific analyses or changing the list.
  if (summary.kind === "model") {
    await client.retainSoundings(open.map(item => item.soundingId));
    signal.throwIfAborted();
  }
  for (const [index, item] of options.selected.entries()) {
    signal.throwIfAborted();
    let source = summary;
    if (summary.kind === "model" && item.value !== summary.forecastHour) {
      if (options.model === undefined || summary.latitude === null || summary.longitude === null) throw new Error("This sounding has no forecast location.");
      source = open.find((entry) => entry.kind === "model" && entry.model === summary.model && entry.runTime === summary.runTime
        && entry.forecastHour === item.value && entry.latitude === summary.latitude && entry.longitude === summary.longitude && entry.box === undefined)
        ?? await client.openSounding({ kind: "model", model: options.model.key, runTime: summary.runTime,
        forecastHour: item.value, latitude: summary.latitude, longitude: summary.longitude, location: summary.location },
      crypto.randomUUID().replaceAll("-", ""), signal);
    }
    signal.throwIfAborted();
    const timeIndex = summary.kind === "model" ? 0 : item.value;
    const viewId = timeIndex === 0 ? source.soundingId : `${source.soundingId}:${String(timeIndex)}`;
    const state = useSoundingStore.getState().views[viewId] ?? INITIAL_VIEW;
    const view: ViewRequest = { ...options.view, edits: state.history[state.cursor] ?? [], timeIndex };
    delete view.drag; delete view.animationRange;
    loaded.push({ summary: source, view, label: item.label });
    report("loading", index + 1);
  }
  // Inspect the complete selection before choosing the common display range.
  let largest = 0;
  report("scaling", 0);
  for (const [index, item] of loaded.entries()) {
    const view = await client.renderSoundingView(item.summary.soundingId, options.choice, "hd", item.view, options.colorStyle, signal);
    signal.throwIfAborted();
    for (const level of view.inspection.levels) {
      if (level.hodograph !== null && level.windSpeed !== null) largest = Math.max(largest, level.windSpeed);
    }
    report("scaling", index + 1);
  }
  const radius = Math.max(40, Math.ceil((largest + 15) / 20) * 20);
  if (radius > 500) throw new Error("The selected profiles exceed the supported animation range.");
  const frames: AnimationFrame[] = [];
  let bytes = 0, width = 0, height = 0;
  report("rendering", 0);
  for (const [index, item] of loaded.entries()) {
    signal.throwIfAborted();
    const view = await client.renderSoundingView(item.summary.soundingId, options.choice, options.quality,
      { ...item.view, animationRange: radius }, options.colorStyle, signal);
    signal.throwIfAborted();
    if (index > 0 && (view.width !== width || view.height !== height)) throw new Error("Sounding frame dimensions changed. Try one image quality for the entire selection.");
    width = view.width; height = view.height; bytes += view.image.size;
    if (bytes > 128 * 1024 * 1024) throw new Error("The lossless frames exceed 128 MiB. Choose fewer hours or HD quality.");
    const validTime = item.summary.kind === "model" ? item.summary.validTime : null;
    frames.push({ image: view.image, label: `${item.label}${validTime == null ? "" : ` · ${compactUtc(validTime)} UTC`}` });
    report("rendering", index + 1);
  }
  return { frames, width, height };
}

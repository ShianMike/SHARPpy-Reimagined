import type { BackendClient } from "../../lib/backend/client";
import type { ForecastModel, ImageMode, RenderChoice, SoundingColorStyle, SoundingSummary } from "../../types/soundings";
import type { ViewRequest } from "../../types/soundingView";
import type { AnimationFrame } from "../animation/encodeAnimation";
import { useSoundingStore } from "../../stores/soundingStore";
import { compactUtc, forecastLabel, releaseTime } from "./soundingTime";
import { INITIAL_VIEW } from "./viewHistory";

export const SOUNDING_ANIMATION_FRAME_LIMIT = 12;

export interface SoundingFrameChoice { value: number; label: string; timeLabel?: string }
export interface SoundingAnimationProgress { phase: "loading" | "scaling" | "rendering" | "encoding"; done: number; total: number }
export interface PreparedSoundings { frames: AnimationFrame[]; width: number; height: number }
export interface SoundingAnimationOptions {
  summary: SoundingSummary; model: ForecastModel | undefined; selected: readonly SoundingFrameChoice[];
  choice: RenderChoice; quality: ImageMode; colorStyle: SoundingColorStyle; view: ViewRequest;
}

/** Resolve against the unfiltered backend catalog, whose keys identify native model summaries. */
export function resolveSoundingAnimationModel(summary: SoundingSummary, models: readonly ForecastModel[]): ForecastModel | undefined {
  if (summary.kind !== "model") return undefined;
  const provider = summary.provider?.toLowerCase();
  const legacyModel = summary.model?.toLowerCase();
  return models.find((model) => model.key.toLowerCase() === provider)
    ?? models.find((model) => model.key.toLowerCase() === legacyModel)
    ?? models.find((model) => model.label.toLowerCase() === legacyModel);
}

function animationSource(summary: SoundingSummary, model: ForecastModel | undefined) {
  if (summary.kind === "model") {
    const { runTime, latitude, longitude } = summary;
    if (model === undefined || !model.soundingsAvailable || summary.box !== undefined
      || runTime === null || latitude === null || longitude === null) return undefined;
    const cycle = new Date(runTime).getUTCHours();
    const limit = model.maxForecastHours[cycle];
    // An opened sounding may be from an archive outside the catalog's recent runs.
    if (limit === undefined || !model.cycles.includes(cycle) || releaseTime(runTime.slice(0, 10), cycle) !== runTime) return undefined;
    const choices = model.forecastHours.filter((hour) => hour <= limit).map((value) => ({ value, label: forecastLabel(value) }));
    return { choices, point: { model, runTime, latitude, longitude } };
  }
  const choices = summary.timeCount < 2 ? [] : Array.from({ length: summary.timeCount }, (_, value) => {
    const time = summary.times?.[value];
    const profile = String(value + 1);
    if (time == null) return { value, label: `Profile ${profile}` };
    const timeLabel = `${compactUtc(time)} UTC`;
    return { value, label: `${profile} · ${timeLabel}`, timeLabel };
  });
  return { choices, point: undefined };
}

export function soundingFrameChoices(summary: SoundingSummary, model: ForecastModel | undefined): SoundingFrameChoice[] {
  return animationSource(summary, model)?.choices ?? [];
}

/** Source profiles are prepared first; both plots then use one fixed extent. */
export async function prepareSoundingAnimation(options: SoundingAnimationOptions, client: BackendClient,
  signal: AbortSignal, progress: (state: SoundingAnimationProgress) => void): Promise<PreparedSoundings> {
  signal.throwIfAborted();
  if (options.selected.length < 2 || options.selected.length > SOUNDING_ANIMATION_FRAME_LIMIT) {
    throw new Error(`Choose between 2 and ${String(SOUNDING_ANIMATION_FRAME_LIMIT)} sounding frames.`);
  }
  const { summary } = options;
  const animation = animationSource(summary, options.model);
  if (animation === undefined || !options.selected.every((item) => animation.choices.some((choice) => choice.value === item.value))) {
    throw new Error("A selected sounding hour is unavailable.");
  }
  const { point } = animation;
  const loaded: { summary: SoundingSummary; view: ViewRequest; label: string }[] = [];
  const report = (phase: SoundingAnimationProgress["phase"], done: number): void => { progress({ phase, done, total: options.selected.length }); };
  report("loading", 0);
  const open = useSoundingStore.getState().open;
  // Confirm retention before adding animation sources; this protects visible
  // profiles without requesting their scientific analyses or changing the list.
  if (point !== undefined) {
    await client.retainSoundings(open.map(item => item.soundingId));
    signal.throwIfAborted();
  }
  for (const [index, item] of options.selected.entries()) {
    signal.throwIfAborted();
    let source = summary;
    if (point !== undefined && item.value !== summary.forecastHour) {
      const { model, runTime, latitude, longitude } = point;
      const sameFrame = open.filter((entry) => entry.kind === "model" && entry.box === undefined && entry.runTime === runTime
        && entry.forecastHour === item.value && entry.latitude === latitude && entry.longitude === longitude);
      source = sameFrame.find((entry) => entry.provider?.toLowerCase() === model.key.toLowerCase())
        // A saved label can identify a legacy profile only when it lacks a canonical provider key.
        ?? sameFrame.find((entry) => entry.provider === null && resolveSoundingAnimationModel(entry, [model]) !== undefined)
        ?? await client.openSounding({ kind: "model", model: model.key, runTime, forecastHour: item.value,
          latitude, longitude, location: summary.location }, crypto.randomUUID().replaceAll("-", ""), signal);
    }
    signal.throwIfAborted();
    const timeIndex = point === undefined ? item.value : 0;
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
    signal.throwIfAborted();
    const wind = await client.measureSoundingViewWind(item.summary.soundingId, options.choice, item.view, options.colorStyle, signal);
    signal.throwIfAborted();
    largest = Math.max(largest, wind);
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

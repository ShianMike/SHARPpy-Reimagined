import { useEffect, useRef } from "react";
import { useForecastStore } from "../stores/forecastStore";
import { useUiStore } from "../stores/uiStore";
import type { FieldImage } from "./layers/registry";
import type { MapRuntime } from "./runtime/MapRuntime";

export const FRAME_BLEND_MS = 220;

/** Blend only adjacent forecast frames, without re-rendering React on animation ticks. */
export function useFieldTransition(runtime: MapRuntime | null, image: FieldImage | null, model: string | undefined): void {
  const smooth = useUiStore((state) => state.smoothPlayback);
  const playing = useForecastStore((state) => state.playing);
  const previous = useRef<{ image: FieldImage | null; model: string | undefined }>({ image: null, model });
  useEffect(() => {
    const prior = previous.current;
    previous.current = { image, model };
    const blend = smooth && playing && !window.matchMedia("(prefers-reduced-motion: reduce)").matches
      && prior.image !== null && image !== null && prior.model === model
      && prior.image.frame.product === image.frame.product && prior.image.frame.runTime === image.frame.runTime
      && prior.image.frame.forecastHour !== image.frame.forecastHour;
    runtime?.setScene({ field: image, previousField: blend ? prior.image : null, fieldMix: blend ? 0 : 1 });
    if (!blend || runtime === null) return;
    let handle: number;
    const start = performance.now();
    const tick = (now: number): void => {
      const mix = Math.min(1, (now - start) / FRAME_BLEND_MS);
      runtime.setScene({ fieldMix: mix, ...(mix === 1 ? { previousField: null } : {}) });
      if (mix < 1) handle = requestAnimationFrame(tick);
    };
    handle = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(handle);
      runtime.setScene({ previousField: null, fieldMix: 1 });
    };
  }, [runtime, image, model, smooth, playing]);
}

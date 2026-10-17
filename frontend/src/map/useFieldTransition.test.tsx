// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useForecastStore } from "../stores/forecastStore";
import { useUiStore } from "../stores/uiStore";
import type { FieldImage } from "./layers/registry";
import type { MapRuntime } from "./runtime/MapRuntime";
import { FRAME_BLEND_MS, useFieldTransition } from "./useFieldTransition";

const savedUi = useUiStore.getState();
const savedForecast = useForecastStore.getState();
afterEach(() => { cleanup(); useUiStore.setState(savedUi); useForecastStore.setState(savedForecast); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const image = (hour: number, product = "refc"): FieldImage => ({ frame: { product, runTime: "2026-10-17T00:00:00Z", forecastHour: hour } } as FieldImage);

it("blends forecast frames and releases the previous texture when finished or switched to Fast", () => {
  const setScene = vi.fn();
  const runtime = { setScene } as unknown as MapRuntime;
  let tick: FrameRequestCallback = () => undefined;
  vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => { tick = callback; return 1; }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.spyOn(performance, "now").mockReturnValue(100);
  useUiStore.setState({ smoothPlayback: true });
  useForecastStore.setState({ playing: true });
  const first = image(0); const second = image(1);
  const hook = renderHook(({ field }) => { useFieldTransition(runtime, field, "hrrr"); }, { initialProps: { field: first } });
  hook.rerender({ field: second });
  expect(setScene).toHaveBeenLastCalledWith({ field: second, previousField: first, fieldMix: 0 });
  act(() => { tick(100 + FRAME_BLEND_MS / 2); });
  expect(setScene).toHaveBeenLastCalledWith({ fieldMix: 0.5 });
  act(() => { tick(100 + FRAME_BLEND_MS); });
  expect(setScene).toHaveBeenLastCalledWith({ fieldMix: 1, previousField: null });
  hook.rerender({ field: image(2) });
  act(() => { useUiStore.getState().setSmoothPlayback(false); });
  expect(cancelAnimationFrame).toHaveBeenCalled();
  expect(setScene).toHaveBeenLastCalledWith(expect.objectContaining({ previousField: null, fieldMix: 1 }));
});

it.each(["fast", "paused", "reduced motion", "new product", "new model"])("switches immediately for %s", (reason) => {
  const setScene = vi.fn();
  vi.stubGlobal("requestAnimationFrame", vi.fn());
  vi.stubGlobal("matchMedia", () => ({ matches: reason === "reduced motion" }));
  useUiStore.setState({ smoothPlayback: reason !== "fast" });
  useForecastStore.setState({ playing: reason !== "paused" });
  const hook = renderHook(({ field, model }) => { useFieldTransition({ setScene } as unknown as MapRuntime, field, model); },
    { initialProps: { field: image(0), model: "hrrr" } });
  hook.rerender({ field: image(1, reason === "new product" ? "temp-2m" : "refc"), model: reason === "new model" ? "rrfs-a" : "hrrr" });
  expect(requestAnimationFrame).not.toHaveBeenCalled();
  expect(setScene).toHaveBeenLastCalledWith(expect.objectContaining({ previousField: null, fieldMix: 1 }));
});

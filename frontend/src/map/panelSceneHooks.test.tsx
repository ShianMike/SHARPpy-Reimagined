// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import models from "@contracts/public/soundings/models.catalog.json";
import fieldPng from "@contracts/public/map/field.shrf?url&inline";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { parseFieldFrame } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import type { FieldFrame } from "../types/map";
import { useForecastStore } from "../stores/forecastStore";
import { useMapStore } from "../stores/mapStore";
import { useLiveTrackingStore } from "../stores/liveTrackingStore";
import { domainPath, useFieldImage, usePngImage } from "./panelScene";

const savedMap = useMapStore.getState();
const savedLive = useLiveTrackingStore.getState();
const savedForecast = useForecastStore.getState();

afterEach(() => {
  cleanup();
  useMapStore.setState(savedMap);
  useLiveTrackingStore.setState(savedLive);
  useForecastStore.setState(savedForecast);
  vi.unstubAllGlobals();
});

const bytes = (url: string): ArrayBuffer => Uint8Array.from(atob(url.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer;

it("exposes image decode failure, ignores an obsolete rejection, and retries the displayed frame", async () => {
  const refetch = vi.fn();
  let rejectOld: (error: Error) => void = () => undefined;
  const decode = vi.fn().mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject; }))
    .mockRejectedValueOnce(new Error("corrupt PNG")).mockResolvedValue({ width: 2, height: 2 });
  vi.stubGlobal("createImageBitmap", decode);
  const bounds = { west: -100, east: -90, south: 30, north: 40 };
  const older = { png: new Blob(["old"]), bounds, validTime: "2026-10-04T18:00:00Z" };
  const newer = { png: new Blob(["new"]), bounds, validTime: "2026-10-04T19:00:00Z" };
  const hook = renderHook(({ data, enabled }) => usePngImage({ data, isError: false, refetch }, enabled, "radar"),
    { initialProps: { data: older, enabled: true } });
  expect(hook.result.current.decodedCurrent).toBe(false);
  hook.rerender({ data: newer, enabled: true });
  await waitFor(() => { expect(hook.result.current.issue).toContain("could not be decoded"); });
  expect(hook.result.current.decodedCurrent).toBe(false);
  await act(async () => { rejectOld(new Error("obsolete")); await Promise.resolve(); });
  expect(hook.result.current.issue).toContain("could not be decoded");
  act(() => { hook.result.current.retry(); });
  await waitFor(() => { expect(hook.result.current.image).not.toBeNull(); });
  expect(hook.result.current.issue).toBeNull();
  expect(hook.result.current.decodedCurrent).toBe(true);
  expect(refetch).toHaveBeenCalledTimes(1);
  hook.rerender({ data: newer, enabled: false });
  expect(hook.result.current.image).toBeNull();
  expect(hook.result.current.decodedCurrent).toBe(false);
  act(() => { hook.result.current.retry(); });
  expect(refetch).toHaveBeenCalledTimes(1);
});

it("clears an earlier radar image until the newly selected frame finishes decoding", async () => {
  let finish: (image: { width: number; height: number }) => void = () => undefined;
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValueOnce({ width: 2, height: 2 })
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })));
  const bounds = { west: -100, east: -90, south: 30, north: 40 };
  const older = { png: new Blob(["old"]), bounds, validTime: "2026-10-04T18:00:00Z" };
  const newer = { png: new Blob(["new"]), bounds, validTime: "2026-10-04T18:05:00Z" };
  const refetch = vi.fn();
  const hook = renderHook(({ data }) => usePngImage({ data, isError: false, refetch }, true, "radar", false),
    { initialProps: { data: older } });
  await waitFor(() => { expect(hook.result.current.image).not.toBeNull(); });
  hook.rerender({ data: newer });
  expect(hook.result.current.image).toBeNull();
  await act(async () => { finish({ width: 2, height: 2 }); await Promise.resolve(); });
  await waitFor(() => { expect(hook.result.current.decodedCurrent).toBe(true); });
  expect(hook.result.current.image).not.toBeNull();
  hook.unmount();
});

it("uses the same closed grid perimeter without adding a zero-length segment", () => {
  const catalogue = parseModelCatalog(models);
  const reference = catalogue.models.find((model) => model.key === "hrrr");
  if (reference === undefined) throw new Error("Missing model fixture");
  for (const model of catalogue.models.filter((item) => item.key === "hrrr" || item.key === "nam-3km-conus" || item.key === "rrfs-a" || item.key.startsWith("refs-m"))) {
    const path = domainPath(model);
    expect(path).toBe(model.outline);
    expect(path).toEqual(reference.outline);
    expect(path?.length).toBeGreaterThan(100);
    expect(path?.[0]).toEqual(path?.at(-1));
    expect(domainPath(model)).toBe(path);
  }
});

it("shares a frame across panels and reopens, colouring only when its Sharp image is read", () => {
  const construct = vi.fn(function (data: Uint8ClampedArray, width: number, height: number) { return { data, width, height }; });
  vi.stubGlobal("ImageData", construct);
  const frame = parseFieldFrame(bytes(fieldPng));
  const palette = parseMapCatalog(catalog).products[0]?.palette;
  if (palette === undefined) throw new Error("Fixture palette missing");
  const first = renderHook(() => useFieldImage(frame, palette));
  const second = renderHook(() => useFieldImage(frame, palette));
  expect(second.result.current).toBe(first.result.current);
  expect(construct).not.toHaveBeenCalled();
  expect(second.result.current?.image).toBe(first.result.current?.image);
  expect(construct).toHaveBeenCalledTimes(1);
  const image = first.result.current;
  first.unmount(); second.unmount();
  const initialProps: { selected: FieldFrame | undefined } = { selected: frame };
  const reopened = renderHook(({ selected }) => useFieldImage(selected, palette), { initialProps });
  expect(reopened.result.current).toBe(image);
  reopened.rerender({ selected: undefined });
  expect(reopened.result.current).toBeNull();
  reopened.rerender({ selected: { ...frame, cells: frame.cells.slice() } });
  expect(reopened.result.current === image).toBe(false);
  expect(construct).toHaveBeenCalledTimes(1);
  expect(reopened.result.current?.image).not.toBe(image?.image);
  expect(construct).toHaveBeenCalledTimes(2);
});

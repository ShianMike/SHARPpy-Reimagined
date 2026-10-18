// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { useGraphicsStore } from "../stores/graphicsStore";
import { cachedPng, decodePng, radarValues } from "./pngImages";
import * as radar from "./radarValues";

const graphics = useGraphicsStore.getState();
afterEach(() => { useGraphicsStore.setState(graphics, true); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("shares an in-flight decode across panels, reuses ready bitmaps, and retries a failed decode", async () => {
  const bitmap = { width: 1, height: 1 };
  const create = vi.fn().mockResolvedValue(bitmap);
  vi.stubGlobal("createImageBitmap", create);
  const png = new Blob(["scan"]);
  const first = decodePng(png);
  expect(decodePng(png)).toBe(first);
  expect(await first).toBe(bitmap);
  expect(cachedPng(png)).toBe(bitmap);
  expect(await decodePng(png)).toBe(bitmap);
  expect(create).toHaveBeenCalledTimes(1);
  const broken = new Blob(["broken"]);
  create.mockRejectedValueOnce(new Error("Invalid image"));
  await expect(decodePng(broken)).rejects.toThrow("Invalid image");
  expect(cachedPng(broken)).toBeUndefined();
  expect(await decodePng(broken)).toBe(bitmap);
});

it("bounds decoded pixels by the graphics profile without closing images a panel may still use", async () => {
  useGraphicsStore.setState({ effective: { ...graphics.effective, profile: "low" } });
  const close = vi.fn();
  vi.stubGlobal("createImageBitmap", vi.fn().mockImplementation(() => Promise.resolve({ width: 4096, height: 4096, close })));
  const scans = Array.from({ length: 3 }, (_, index) => new Blob([String(index)]));
  for (const png of scans) await decodePng(png);
  expect(cachedPng(scans[0] ?? new Blob())).toBeUndefined();
  expect(cachedPng(scans[1] ?? new Blob())).toBeDefined();
  expect(cachedPng(scans[2] ?? new Blob())).toBeDefined();
  expect(close).not.toHaveBeenCalled();
});

it("shares native samples across observers and counts their memory in the existing image budget", async () => {
  useGraphicsStore.setState({ effective: { ...graphics.effective, profile: "low" } });
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 4096, height: 4096 }));
  const samples = { width: 4096, height: 4096, rows: new Uint8Array(4096 * 4097), values: [], folded: null, lowerBounds: new Set<number>() };
  const decode = vi.spyOn(radar, "decodeRadarValues").mockResolvedValue(samples);
  const first = new Blob(["first native scan"]), second = new Blob(["second native scan"]);
  expect(await Promise.all([radarValues(first), radarValues(first)])).toEqual([samples, samples]);
  expect(decode).toHaveBeenCalledTimes(1);
  await radarValues(second);
  expect(cachedPng(first)).toBeUndefined();
  expect(cachedPng(second)).toBeDefined();
});

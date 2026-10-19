// @vitest-environment jsdom
// Opt-in CPU benchmark (from frontend/):
// $env:SHARPPY_FIELD_BENCHMARK='1'; npm exec -- vitest run src/map/fieldImages.performance.test.tsx --maxWorkers=1 --reporter=verbose --disableConsoleIntercept
import { Buffer } from "node:buffer";
import { env, versions } from "node:process";
import catalog from "@contracts/public/map/catalog.json";
import fixture from "@contracts/public/map/field.shrf?url&inline";
import { BitmapLayer } from "@deck.gl/layers";
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { parseFieldFrame } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import tokensCss from "../styles/tokens.css?raw";
import { cssTokenReader } from "../test/cssTokens";
import type { FieldFrame, Palette } from "../types/map";
import { readCanvasTokens } from "./canvasTokens";
import { buildLut, colourize, LUT_SIZE } from "./field/colourize";
import { LAYER_REGISTRY } from "./layers/descriptors";
import { buildDeckLayers, defaultLayerStates, NO_SCENE_DATA, type FieldImage, type LayerBuildContext } from "./layers/registry";
import { useFieldImage } from "./panelScene";
import { prepareFieldImage } from "./fieldImages";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const products = parseMapCatalog(catalog).products;
const source = parseFieldFrame(Uint8Array.from(atob(fixture.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);
const read = cssTokenReader(tokensCss);
const context: LayerBuildContext = {
  ...NO_SCENE_DATA, stations: [], labels: null, selected: null, tokens: readCanvasTokens(read),
  labelFont: read("--font-mono"), placeLabels: [], span: 360,
  basemap: { visible: true, opacity: 1 }, weatherShown: true,
};

function scene(field: FieldImage | null, smoothing: boolean, previousField: FieldImage | null = null): BitmapLayer {
  const states = defaultLayerStates();
  states["model-field"] = { visible: true, opacity: 0.5, smoothing };
  const id = smoothing && field?.palette.units !== "flag" ? "model-field-smooth" : "model-field";
  const layer = buildDeckLayers(LAYER_REGISTRY, states, { ...context, field, previousField, fieldMix: previousField === null ? 1 : 0.5 })
    .find((item): item is BitmapLayer => item instanceof BitmapLayer && item.id === id);
  if (layer === undefined) throw new Error("Missing model-field layer");
  return layer;
}

function sameBytes(actual: Uint8Array | Uint8ClampedArray, expected: Uint8Array | Uint8ClampedArray): void {
  expect(Buffer.compare(Buffer.from(actual.buffer, actual.byteOffset, actual.byteLength),
    Buffer.from(expected.buffer, expected.byteOffset, expected.byteLength))).toBe(0);
}

function checkImage(image: ImageData, frame: FieldFrame, palette: Palette, smoothing: boolean): void {
  expect([image.width, image.height, image.data.byteLength]).toEqual([frame.width, frame.height, frame.cells.length * 4]);
  if (!smoothing || palette.units === "flag") {
    sameBytes(image.data, colourize(frame, palette));
    return;
  }
  const bytes = new DataView(image.data.buffer, image.data.byteOffset, image.data.byteLength);
  for (let i = 0; i < frame.cells.length; i++) {
    if (bytes.getUint16(i * 4) !== frame.cells[i]
      || bytes.getUint8(i * 4 + 2) !== (frame.cells[i] === frame.nodata ? 0 : 255)
      || bytes.getUint8(i * 4 + 3) !== 255) throw new Error(`Smooth changed cell or mask at ${String(i)}`);
  }
}

function checkPalette(layer: BitmapLayer, palette: Palette): void {
  const image = (layer.props as typeof layer.props & { fieldPalette: ImageData }).fieldPalette;
  expect([image.width, image.height]).toEqual(palette.stepped ? [palette.stops.length, 3] : [LUT_SIZE, 1]);
  if (!palette.stepped) { sameBytes(image.data, buildLut(palette)); return; }
  const size = palette.stops.length;
  for (const [i, [value, colour]] of palette.stops.entries()) {
    sameBytes(image.data.subarray(i * 4, i * 4 + 4), new Uint8Array([...Buffer.from(colour.slice(1), "hex"), 255]));
    const low = (size + i) * 4, high = (size * 2 + i) * 4;
    const edge = Uint8Array.of(image.data[low] ?? 0, image.data[low + 1] ?? 0, image.data[low + 2] ?? 0, image.data[high] ?? 0);
    expect(new DataView(edge.buffer).getFloat32(0, true)).toBe(Math.fround(value));
    expect([image.data[low + 3], image.data[high + 1], image.data[high + 2], image.data[high + 3]]).toEqual([255, 0, 0, 255]);
  }
}

function imageDataShim(): { allocations: number } {
  const counter = { allocations: 0 };
  vi.stubGlobal("ImageData", class {
    constructor(public data: Uint8ClampedArray, public width: number, public height: number) { counter.allocations++; }
  });
  return counter;
}

it("keeps lazy and prepared scene bytes exact for every committed palette, continuous colours, and flags", async () => {
  const counter = imageDataShim();
  const first = products[0];
  if (first === undefined) throw new Error("Missing committed palette");
  const palettes = [...products.map((product) => product.palette),
    { ...first.palette, stepped: false }, { ...first.palette, units: "flag" }];
  // Every uint16 code, including a non-default sentinel, passes through the real scene builders.
  const cells = Uint16Array.from({ length: 65536 }, (_, i) => i);
  for (const nodata of [source.nodata, 255]) {
    const frame = { ...source, width: 256, height: 256, cells, nodata };
    for (const palette of palettes) {
      const allocations = counter.allocations;
      const hook = renderHook(() => useFieldImage(frame, palette));
      try {
        expect(counter.allocations).toBe(allocations);
        for (const smoothing of [true, false, true]) {
          await prepareFieldImage(frame, palette, smoothing, new AbortController().signal);
          const firstLayer = scene(hook.result.current, smoothing);
          const firstImage = firstLayer.props.image as ImageData;
          checkImage(firstImage, frame, palette, smoothing);
          if (smoothing && palette.units !== "flag") checkPalette(firstLayer, palette);
          const ready = counter.allocations;
          hook.rerender();
          expect(scene(hook.result.current, smoothing).props.image).toBe(firstImage);
          expect(counter.allocations).toBe(ready);
        }
      } finally { hook.unmount(); }
    }
  }
}, 15_000);

it.each([false, true])("cancels a partially packed image without publishing it, then shares a complete lazy image (%s)", async (smoothing) => {
  vi.useFakeTimers();
  const counter = imageDataShim();
  const palette = products[0]?.palette;
  if (palette === undefined) throw new Error("Missing committed palette");
  const frame = { ...source, width: 2048, height: 1024, cells: new Uint16Array(2048 * 1024).fill(256) };
  const controller = new AbortController();
  const preparing = prepareFieldImage(frame, palette, smoothing, controller.signal);
  const stopped = expect(preparing).rejects.toMatchObject({ name: "AbortError" });
  vi.spyOn(performance, "now").mockImplementation((() => { let now = 0; return () => now += 5; })());
  await vi.advanceTimersByTimeAsync(0);
  expect(vi.getTimerCount()).toBe(1);
  controller.abort();
  await vi.runAllTimersAsync();
  await stopped;
  expect(counter.allocations).toBe(0);
  const hook = renderHook(() => useFieldImage(frame, palette));
  const layer = scene(hook.result.current, smoothing);
  checkImage(layer.props.image as ImageData, frame, palette, smoothing);
  const ready = counter.allocations;
  await prepareFieldImage(frame, palette, smoothing, new AbortController().signal);
  expect(vi.getTimerCount()).toBe(0);
  expect(scene(hook.result.current, smoothing).props.image).toBe(layer.props.image);
  expect(counter.allocations).toBe(ready);
});

// Isolates the existing Smooth core loop; equality is checked against the real scene image outside timings.
function smoothBytes(frame: FieldFrame): Uint8ClampedArray {
  const bytes = new Uint8ClampedArray(frame.cells.length * 4);
  for (let i = 0; i < frame.cells.length; i++) {
    const cell = frame.cells[i] ?? frame.nodata;
    bytes[i * 4] = cell >> 8;
    bytes[i * 4 + 1] = cell & 255;
    bytes[i * 4 + 2] = cell === frame.nodata ? 0 : 255;
    bytes[i * 4 + 3] = 255;
  }
  return bytes;
}

it.skipIf(env.SHARPPY_FIELD_BENCHMARK !== "1")("pairs lazy and prepared production-shape scenes against their core packing loops", async () => {
  const iterations = Number(env.SHARPPY_FIELD_ITERS ?? 5);
  if (!Number.isInteger(iterations) || iterations < 2 || iterations > 20) throw new Error("SHARPPY_FIELD_ITERS must be 2 to 20");
  const counter = imageDataShim();
  const preparationTasks: number[] = [];
  // Time the synchronous continuation of each real preparation yield; this scheduler shim uses Windows timers.
  vi.stubGlobal("scheduler", { yield: () => new Promise<void>((resolve) => {
    setTimeout(() => { const start = performance.now(); resolve(); queueMicrotask(() => { preparationTasks.push(performance.now() - start); }); }, 0);
  }) });
  const width = 2048, height = 1024;
  const cells = new Uint16Array(width * height);
  // Tile the committed numeric field without changing any cell or its mask; this is not a native-resolution weather sample.
  for (let y = 0; y < height; y++) {
    const row = source.cells.subarray((y % source.height) * source.width, (y % source.height + 1) * source.width);
    for (let x = 0; x < width; x += source.width) cells.set(row.subarray(0, Math.min(source.width, width - x)), y * width + x);
  }
  const selected = ["refc", "tmp-2m", "hgt-wind-500", "rh-2m"].map((key) => {
    const product = products.find((item) => item.key === key);
    if (product === undefined) throw new Error(`Missing committed product ${key}`);
    return product;
  });
  const base = selected[0];
  if (base === undefined) throw new Error("Missing committed default palette");
  const cases = [
    { name: "one-panel", products: [base], shared: false },
    { name: "four-distinct-products", products: selected, shared: false },
    { name: "four-matching-panels", products: [base, base, base, base], shared: true },
    { name: "synthetic-continuous", products: [{ ...base, palette: { ...base.palette, stepped: false } }], shared: false },
    { name: "synthetic-occurrence-flag", products: [{ ...base, palette: { ...base.palette, units: "flag" } }], shared: false },
  ];
  const results: object[] = [];
  for (const scenario of cases) {
    for (const smoothing of [false, true]) {
      const unique = scenario.shared ? [scenario.products[0] ?? base] : scenario.products;
      const lanes = [false, true].map((prepared) => ({ prepared, palettes: unique.map((product) => ({ ...product.palette })),
        previous: [] as (FieldImage | null)[], samples: [] as object[], coldStart: undefined as object | undefined }));
      for (let iteration = -2; iteration < iterations; iteration++) {
        const referenceFrames = unique.map((product) => ({ ...source, width, height, cells, product: product.key, forecastHour: iteration + 2 }));
        const coreStart = performance.now();
        const expected = referenceFrames.map((frame, i) => smoothing && unique[i]?.palette.units !== "flag"
          ? smoothBytes(frame) : colourize(frame, unique[i]?.palette ?? base.palette));
        const coreEnd = performance.now();
        // Alternate actual operation order; each lane has its own cold frame and stable palette identities.
        for (const lane of iteration % 2 === 0 ? lanes : [...lanes].reverse()) {
          const frames = referenceFrames.map((frame) => ({ ...frame }));
          const beforePreparation = counter.allocations;
          const tasksBefore = preparationTasks.length;
          const prepareStart = performance.now();
          if (lane.prepared) await Promise.all(frames.map((frame, i) =>
            prepareFieldImage(frame, lane.palettes[i] ?? base.palette, smoothing, new AbortController().signal)));
          const prepareEnd = performance.now();
          const tasks = preparationTasks.slice(tasksBefore);
          const preparationAllocations = counter.allocations - beforePreparation;
          const panelFrames = scenario.shared ? scenario.products.map(() => frames[0] ?? source) : frames;
          const panelPalettes = scenario.shared ? scenario.products.map(() => lane.palettes[0] ?? base.palette) : lane.palettes;
          const before = counter.allocations;
          const start = performance.now();
          const hooks = panelFrames.map((frame, i) => renderHook(() => useFieldImage(frame, panelPalettes[i])));
          const lazy = performance.now();
          const lazyAllocations = counter.allocations - before;
          try {
            const layers = hooks.map((hook, i) => scene(hook.result.current, smoothing, lane.previous[i] ?? null));
            const firstEnd = performance.now();
            const firstAllocations = counter.allocations - before;
            const warmStart = performance.now();
            hooks.forEach((hook) => { hook.rerender(); });
            const warm = hooks.map((hook, i) => scene(hook.result.current, smoothing, lane.previous[i] ?? null));
            const warmEnd = performance.now();
            const warmAllocations = counter.allocations - before - firstAllocations;
            expect(lazyAllocations).toBe(0);
            expect(warmAllocations).toBe(0);
            if (lane.prepared) expect(firstAllocations).toBe(0);
            const paletteAllocations = iteration === -2 && smoothing && lane.palettes.every((palette) => palette.units !== "flag") ? frames.length : 0;
            expect(firstAllocations + preparationAllocations).toBe(frames.length + paletteAllocations);
            for (const [i, layer] of layers.entries()) {
              const image = layer.props.image as ImageData;
              const reference = expected[scenario.shared ? 0 : i];
              if (reference === undefined) throw new Error("Missing core reference image");
              sameBytes(image.data, reference);
              expect(warm[i]?.props.image).toBe(image);
              const palette = panelPalettes[i] ?? base.palette;
              if (smoothing && palette.units !== "flag") checkPalette(layer, palette);
              if (scenario.shared) expect(image).toBe(layers[0]?.props.image);
            }
            const sample = { core_ms: coreEnd - coreStart, prepare_wall_ms: lane.prepared ? prepareEnd - prepareStart : 0,
              prepare_tasks: tasks.length, max_prepare_task_ms: Math.max(0, ...tasks),
              lazy_hooks_ms: lazy - start, first_layers_ms: firstEnd - lazy, first_scene_ms: firstEnd - start, warm_scene_ms: warmEnd - warmStart,
              image_allocations_prepare: preparationAllocations, image_allocations_first: firstAllocations, image_allocations_warm: warmAllocations };
            if (iteration === -2) lane.coldStart = sample;
            if (iteration >= 0) lane.samples.push(sample);
            lane.previous = hooks.map((hook) => hook.result.current);
          } finally { hooks.forEach((hook) => { hook.unmount(); }); cleanup(); }
        }
      }
      for (const lane of lanes) results.push({ case: scenario.name, requested_mode: smoothing ? "smooth" : "sharp", prepared: lane.prepared,
          actual_mode: smoothing && scenario.products.every((product) => product.palette.units !== "flag") ? "smooth" : "sharp",
          panels: scenario.products.length, unique_fields: scenario.shared ? 1 : scenario.products.length,
          palettes: scenario.products.map((product) => product.key), cold_start: lane.coldStart, samples: lane.samples });
    }
  }
  console.log("FIELD_IMAGE_BENCHMARK " + JSON.stringify({ runtime: versions, iterations, warmups: 2,
    dataset: "production-shape: tiled committed field.shrf cells, original scale/offset/nodata, alternate committed palettes are encoding workloads only",
    width, height, cells_per_field: cells.length, raw_bytes_per_field: cells.byteLength, rgba_bytes_per_image: cells.length * 4,
    scope: "Node/jsdom CPU only; ImageData shim stores bytes without browser validation/upload. No GPU, WebView2, network, decode, or presented-frame timing.",
    scene: "Actual useFieldImage hook and complete buildDeckLayers registry; empty non-field overlays, no runtime/GPU initialization. First scene includes React mount; warm scene includes rerender. cold_start has a cold palette and no previous field; samples use new frames, stable warm palettes, and a warm previous field at 50 percent blend.",
    limits: "Raw fixture tiling and byte checks excluded; core runs first; lazy/prepared order alternates. Panels share source cells; each lane holds current and previous fields. Preparation uses a timer-backed scheduler shim; wall time includes yields, not a CPU-throughput speedup. No peak-memory or GPU-gain claim.",
    frame_budget_ms: 1000 / 60, bytes_equal: true, results,
  }));
}, 60_000);

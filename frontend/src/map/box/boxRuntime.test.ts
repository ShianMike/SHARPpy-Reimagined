// @vitest-environment jsdom
import type { Map as MapLibreMap } from "maplibre-gl";
import { afterEach, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { wireBoxSelection } from "./boxRuntime";

vi.mock("../canvasTokens", () => ({ documentTokenReader: () => () => "", readCanvasTokens: () => ({
  colours: { selected: [255, 200, 0, 255], selectedHalo: [255, 200, 0, 50] }, lengths: { domainWidth: 2 },
}) }));
vi.mock("./boxHandles", () => ({ boxHandles: () => ({ update: vi.fn(), dispose: vi.fn(),
  cancel: () => { useBoxSoundingStore.getState().cancelEdit(); },
}) }));
const initialBox = useBoxSoundingStore.getInitialState();
const initialApp = useAppStore.getInitialState();
const initialMap = useMapStore.getInitialState();
afterEach(() => { useBoxSoundingStore.setState(initialBox, true); useAppStore.setState(initialApp, true); useMapStore.setState(initialMap, true); });

function gestureHarness() {
  const canvas = document.createElement("div");
  const mapCanvas = document.createElement("canvas");
  canvas.append(mapCanvas);
  const captured = new Set<number>();
  canvas.setPointerCapture = (id) => { captured.add(id); };
  canvas.hasPointerCapture = (id) => captured.has(id);
  canvas.releasePointerCapture = (id) => { captured.delete(id); };
  let panning = true;
  const fake = {
    getCanvasContainer: () => canvas, getCanvas: () => mapCanvas, isStyleLoaded: () => true,
    getSource: () => ({ setData: vi.fn().mockResolvedValue(undefined) }),
    unproject: ([x, y]: [number, number]) => ({ lng: x / 100 - 100, lat: 40 - y / 100 }),
    project: ({ lng, lat }: { lng: number; lat: number }) => ({ x: (lng + 100) * 100, y: (40 - lat) * 100 }),
    dragPan: { isEnabled: () => panning, enable: () => { panning = true; }, disable: () => { panning = false; } },
    on: vi.fn(), off: vi.fn(),
  } as unknown as MapLibreMap;
  useAppStore.getState().setActiveWorkspace("map");
  useBoxSoundingStore.getState().setMode("box");
  const dispose = wireBoxSelection(fake, 0);
  return { captured, panning: () => panning, dispose, pointer: (type: string, id: number, x: number, y: number) => {
    canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: id, clientX: x, clientY: y, button: 0 }));
  } };
}

it("keeps the first pointer's box and restores panning when another finger touches the map", () => {
  const map = gestureHarness();
  try {
    map.pointer("pointerdown", 1, 100, 100);
    map.pointer("pointermove", 1, 200, 200);
    const draft = useBoxSoundingStore.getState().draft;
    expect(draft).not.toBeNull();
    map.pointer("pointerdown", 2, 300, 300);
    expect(useBoxSoundingStore.getState().draft).toEqual(draft);
    map.pointer("pointerup", 1, 200, 200);
    expect(useBoxSoundingStore.getState()).toMatchObject({ bounds: draft, editing: false, draft: null });
    expect(map.captured.size).toBe(0);
    expect(map.panning()).toBe(true);
  } finally { map.dispose(); }
});

it("cancels an interrupted pointer capture without saving a partial box or locking map panning", () => {
  const map = gestureHarness();
  try {
    map.pointer("pointerdown", 1, 100, 100);
    map.pointer("pointermove", 1, 200, 200);
    map.captured.delete(1);
    map.pointer("lostpointercapture", 1, 200, 200);
    expect(useBoxSoundingStore.getState()).toMatchObject({ bounds: null, draft: null, editing: false });
    expect(map.panning()).toBe(true);
  } finally { map.dispose(); }
});

it.each(["Escape", "blur"])("rolls back a corner edit after %s", (action) => {
  const map = gestureHarness();
  try {
    const bounds = { west: -98, east: -97, south: 35, north: 36 };
    useBoxSoundingStore.getState().commit(bounds);
    useBoxSoundingStore.getState().begin();
    useBoxSoundingStore.getState().preview({ ...bounds, east: -96 });
    window.dispatchEvent(action === "Escape" ? new KeyboardEvent("keydown", { key: "Escape" }) : new Event("blur"));
    expect(useBoxSoundingStore.getState()).toMatchObject({ bounds, draft: null, editing: false });
  } finally { map.dispose(); }
});

it("keeps the selection in sync while source tiles load and after workspace switches", () => {
  const canvas = document.createElement("div");
  const setData = vi.fn<(data: unknown) => Promise<void>>().mockResolvedValue(undefined);
  const listeners = new Map<string, () => void>();
  let source: unknown;
  let fullyLoaded = true;
  const fake = { getCanvasContainer: () => canvas, isStyleLoaded: () => fullyLoaded,
    getSource: () => source, addSource: () => { source = { setData }; }, addLayer: vi.fn(),
    on: (event: string, action: () => void) => { listeners.set(event, action); },
    off: (event: string) => { listeners.delete(event); },
  } as unknown as MapLibreMap;
  useAppStore.getState().setActiveWorkspace("map");
  useBoxSoundingStore.getState().setMode("box");
  const dispose = wireBoxSelection(fake, 0);
  const bounds = { west: -98, east: -97, south: 35, north: 36 };
  fullyLoaded = false; // Source loading must not be mistaken for an uninitialized style.
  useBoxSoundingStore.getState().commit(bounds);
  expect(setData.mock.lastCall?.[0]).toMatchObject({ features: [{ geometry: { type: "Polygon" } }] });
  useAppStore.getState().setActiveWorkspace("sounding");
  expect(setData.mock.lastCall?.[0]).toMatchObject({ features: [] });
  useAppStore.getState().setActiveWorkspace("map");
  expect(setData.mock.lastCall?.[0]).toMatchObject({ features: [{ geometry: { type: "Polygon" } }] });
  // A genuinely replaced style waits for its ready event, then recreates its source.
  listeners.get("styledataloading")?.();
  source = undefined;
  useBoxSoundingStore.getState().commit({ ...bounds, east: -96 });
  expect(source).toBeUndefined();
  listeners.get("style.load")?.();
  expect(source).toBeDefined();
  dispose();
  expect(listeners.size).toBe(0);
});

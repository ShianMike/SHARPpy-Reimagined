// @vitest-environment jsdom
import type { Map as MapLibreMap } from "maplibre-gl";
import { afterEach, expect, it, vi } from "vitest";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { boxHandles } from "./boxHandles";

interface TestMarker {
  element: HTMLElement;
  setLngLat: (position: readonly [number, number]) => TestMarker;
  fire: (event: string) => void;
}
const { markers } = vi.hoisted(() => ({ markers: [] as TestMarker[] }));
vi.mock("maplibre-gl", () => ({ Marker: class {
  position = { lng: 0, lat: 0 };
  listeners = new Map<string, () => void>();
  element: HTMLElement;
  constructor({ element }: { element: HTMLElement }) { this.element = element; markers.push(this); }
  setLngLat([lng, lat]: readonly [number, number]) { this.position = { lng, lat }; return this; }
  getLngLat() { return this.position; }
  addTo() { document.body.append(this.element); return this; }
  remove() { this.element.remove(); return this; }
  on(event: string, callback: () => void) { this.listeners.set(event, callback); return this; }
  fire(event: string) { this.listeners.get(event)?.(); }
} }));

const bounds = { west: -98, east: -97, south: 35, north: 36 };
const map = {
  project: ({ lng, lat }: { lng: number; lat: number }) => ({ x: lng * 10, y: -lat * 10 }),
  unproject: ([x, y]: [number, number]) => [x / 10, -y / 10],
} as unknown as MapLibreMap;

afterEach(() => {
  useBoxSoundingStore.setState(useBoxSoundingStore.getInitialState(), true);
  markers.length = 0;
  document.body.replaceChildren();
});

it("cancels a corner resize and ignores a late drag end instead of committing a newer draft", () => {
  useBoxSoundingStore.getState().commit(bounds);
  const handles = boxHandles(map);
  handles.update(bounds);
  const corner = markers[2];
  if (corner === undefined) throw new Error("Corner control missing");
  try {
    corner.fire("dragstart");
    corner.setLngLat([-96, 37]);
    corner.fire("drag");
    expect(useBoxSoundingStore.getState()).toMatchObject({ editing: true, draft: { ...bounds, east: -96, north: 37 } });
    handles.cancel();
    expect(useBoxSoundingStore.getState()).toMatchObject({ editing: false, draft: null, bounds });
    expect(corner.element.isConnected).toBe(false);
    useBoxSoundingStore.getState().begin();
    const newer = { ...bounds, west: -99 };
    useBoxSoundingStore.getState().preview(newer);
    corner.fire("dragend");
    expect(useBoxSoundingStore.getState()).toMatchObject({ bounds, draft: newer, editing: true });
  } finally { handles.dispose(); }
});

it("leaves browser arrow shortcuts alone and cancels only its own interrupted resize", () => {
  useBoxSoundingStore.getState().commit(bounds);
  const handles = boxHandles(map);
  handles.update(bounds);
  const corner = markers[2];
  if (corner === undefined) throw new Error("Corner control missing");
  try {
    const revision = useBoxSoundingStore.getState().revision;
    corner.element.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", altKey: true, bubbles: true }));
    expect(useBoxSoundingStore.getState()).toMatchObject({ bounds, revision });
    corner.fire("dragstart");
    corner.setLngLat([-96, 37]);
    corner.fire("drag");
    handles.update(null);
    expect(useBoxSoundingStore.getState()).toMatchObject({ bounds, draft: null, editing: false });
    useBoxSoundingStore.getState().begin();
    handles.cancel(); // This instance no longer owns the edit.
    expect(useBoxSoundingStore.getState().editing).toBe(true);
  } finally { handles.dispose(); }
});

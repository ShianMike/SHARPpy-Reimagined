import type { Map as MapLibreMap } from "maplibre-gl";
import { expect, it, vi } from "vitest";
import { linkCamera } from "./cameraLink";

function mapAt(lng = -97, lat = 35, zoom = 5) {
  const listeners = new Map<string, Set<() => void>>();
  const emit = (event: string) => { listeners.get(event)?.forEach((listener) => { listener(); }); };
  const map = {
    getCenter: () => ({ lng, lat }), getZoom: () => zoom,
    on: (event: string, listener: () => void) => {
      const set = listeners.get(event) ?? new Set(); set.add(listener); listeners.set(event, set);
    },
    off: (event: string, listener: () => void) => { listeners.get(event)?.delete(listener); },
    jumpTo: vi.fn((options: { center: { lng: number; lat: number }; zoom: number }) => {
      ({ lng, lat } = options.center); zoom = options.zoom;
      emit("move"); emit("moveend");
    }),
  };
  return {
    map: map as unknown as MapLibreMap, jumpTo: map.jumpTo, emit,
    move: (nextLng: number) => { lng = nextLng; emit("move"); },
  };
}

it.each([1, 2, 4])("synchronizes %i panels but settles their labels once per completed gesture", (count) => {
  const panels = Array.from({ length: count }, () => ({ ...mapAt(), settled: vi.fn() }));
  const settled = panels.map((panel) => panel.settled);
  const unlinks = panels.map((panel) => linkCamera(panel.map, panel.settled));
  try {
    for (const source of panels) {
      panels.forEach((panel) => { panel.jumpTo.mockClear(); });
      settled.forEach((callback) => { callback.mockClear(); });
      source.emit("move"); // A resize or unchanged camera must not stop another panel's animation.
      expect(panels.every((panel) => panel.jumpTo.mock.calls.length === 0)).toBe(true);
      for (let frame = 0; frame < 60; frame++) {
        source.move(-96 + frame / 100);
        expect(panels.every((panel) => panel.map.getCenter().lng === source.map.getCenter().lng)).toBe(true);
      }
      expect(settled.every((callback) => callback.mock.calls.length === 0)).toBe(true);
      source.emit("moveend");
      expect(settled.every((callback) => callback.mock.calls.length === 1)).toBe(true);
    }
  } finally { unlinks.forEach((unlink) => { unlink(); }); }
});

it("joins the current camera and releases all synchronization listeners when closed", () => {
  const first = mapAt(); const second = mapAt(0, 0, 1);
  const firstSettled = vi.fn(); const secondSettled = vi.fn();
  const unlinkFirst = linkCamera(first.map, firstSettled);
  const unlinkSecond = linkCamera(second.map, secondSettled);
  try {
    expect(second.map.getCenter()).toEqual(first.map.getCenter());
    expect(second.map.getZoom()).toBe(first.map.getZoom());
    unlinkSecond(); second.jumpTo.mockClear();
    first.move(-90); first.emit("moveend");
    second.move(-80); second.emit("moveend");
    expect(second.jumpTo).not.toHaveBeenCalled();
    expect(first.map.getCenter().lng).toBe(-90);
    expect(firstSettled).toHaveBeenCalledTimes(1);
    expect(secondSettled).not.toHaveBeenCalled();
  } finally { unlinkFirst(); unlinkSecond(); }
});

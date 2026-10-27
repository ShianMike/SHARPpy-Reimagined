// @vitest-environment jsdom
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import type { Map as MapLibreMap, MapMouseEvent } from "maplibre-gl";
import { afterEach, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useHoverStore } from "../hover/hoverStore";
import { wireOverlayEvents, type RuntimeEventTarget } from "./runtimeEvents";
import { NO_WATCH } from "./runtimeWiring";

afterEach(() => { vi.unstubAllGlobals(); useHoverStore.setState(useHoverStore.getInitialState(), true); });

it("coalesces map movement into one atomic pick, selects points without a pick, and cancels on leaving, moving and disposal", () => {
  const saved = useAppStore.getState();
  const handlers = new Map<string, (event: MapMouseEvent) => void>();
  const frames = new Map<number, FrameRequestCallback>();
  let next = 0;
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => { frames.set(++next, fn); return next; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames.delete(id); });
  const map = {
    on: (name: string, fn: (event: MapMouseEvent) => void) => { handlers.set(name, fn); },
    off: (name: string) => { handlers.delete(name); },
    isMoving: vi.fn(() => false),
  };
  let initialized = false;
  const overlay = { pickObject: vi.fn(() => {
    if (!initialized) throw new Error("deck.gl: assertion failed.");
    return null;
  }) };
  const target: RuntimeEventTarget = {
    panel: 2, container: document.createElement("div"), primary: false, watch: NO_WATCH,
    errors: [], dragging: false, overlayReady: false, live: (fn) => fn, styleLoaded: vi.fn(), runtimeError: vi.fn(),
  };
  const event = (x: number): MapMouseEvent => ({ point: { x, y: 30 }, lngLat: { lng: -97, lat: 35 } }) as MapMouseEvent;
  const emit = (name: string, x = 50): void => { handlers.get(name)?.(event(x)); };
  const flush = (): void => { const pending = [...frames.values()]; frames.clear(); pending.forEach((fn) => { fn(0); }); };
  const unsubscribe = wireOverlayEvents(map as unknown as MapLibreMap, overlay as unknown as MapLibreOverlay, target, 6);
  let writes = 0;
  const stop = useHoverStore.subscribe(() => { writes += 1; });
  try {
    emit("mousemove");
    flush();
    emit("click");
    expect(overlay.pickObject).not.toHaveBeenCalled();
    expect(useAppStore.getState().selectedPoint).toEqual({ longitude: -97, latitude: 35 });
    initialized = true;
    target.overlayReady = true;
    writes = 0;
    for (let x = 0; x < 100; x++) emit("mousemove", x);
    expect(overlay.pickObject).not.toHaveBeenCalled();
    expect(frames.size).toBe(1);
    flush();
    expect(overlay.pickObject).toHaveBeenCalledExactlyOnceWith({ x: 99, y: 30, radius: 6 });
    expect(writes).toBe(1);
    expect(useHoverStore.getState().pointer).toEqual({ panel: 2, longitude: -97, latitude: 35, x: 99, y: 30 });
    emit("click");
    expect(useAppStore.getState().selectedPoint).toEqual({ longitude: -97, latitude: 35 });
    emit("mousemove");
    useHoverStore.getState().setInspecting(false);
    overlay.pickObject.mockClear();
    writes = 0;
    flush();
    emit("mousemove");
    expect(frames.size).toBe(0);
    expect(overlay.pickObject).not.toHaveBeenCalled();
    expect(useHoverStore.getState().pointer).toBeNull();
    expect(writes).toBe(0);
    useHoverStore.getState().setInspecting(true);
    for (const exit of ["mouseout", "movestart"]) {
      emit("mousemove"); emit(exit);
      expect(frames.size).toBe(0);
      expect(useHoverStore.getState().pointer).toBeNull();
    }
    target.dragging = true;
    emit("mousemove");
    expect(frames.size).toBe(0);
    target.dragging = false;
    map.isMoving.mockReturnValue(true);
    emit("mousemove");
    expect(frames.size).toBe(0);
    map.isMoving.mockReturnValue(false);
    emit("mousemove");
    unsubscribe();
    expect(frames.size).toBe(0);
    expect(handlers.size).toBe(0);
  } finally {
    stop(); unsubscribe(); useAppStore.setState(saved, true);
  }
});

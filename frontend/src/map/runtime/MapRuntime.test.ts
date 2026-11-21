// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { useMapStore } from "../../stores/mapStore";
import { useHoverStore, type HoveredReport } from "../hover/hoverStore";
import { MapRuntime } from "./MapRuntime";

const calls = vi.hoisted(() => [] as string[]);
vi.mock("./maplibreWorker", () => ({}));
vi.mock("maplibre-gl", () => ({ Map: class {
  private center = { lng: -97, lat: 35 };
  private zoom = 4;
  setPadding() {}
  getCenter() { return this.center; }
  getZoom() { return this.zoom; }
  jumpTo(options: { center: [number, number]; zoom: number }) {
    this.center = { lng: options.center[0], lat: options.center[1] }; this.zoom = options.zoom;
    calls.push("saved camera");
  }
  flyTo(options: { center: [number, number]; zoom: number }) { this.jumpTo(options); }
  fitBounds() { calls.push("default camera"); }
  addControl() { calls.push("overlay attached"); }
  remove() {}
} }));
vi.mock("../canvasTokens", () => ({ documentTokenReader: () => () => "monospace", readCanvasTokens: () => ({ lengths: { pickingRadius: 6 } }), readCameraDurationMs: () => 0 }));
vi.mock("./mapOptions", () => ({ mapOptions: () => ({}), panelPixelRatio: () => 1 }));
vi.mock("./runtimeEvents", () => ({ wireMapEvents: () => undefined, createPanelOverlay: () => ({}), wireOverlayEvents: () => () => undefined }));
vi.mock("./cameraLink", () => ({ linkCamera: () => () => undefined }));
vi.mock("../forecastDrawing/forecastDrawingRuntime", () => ({ wireMapDrawingTools: () => () => undefined }));
vi.mock("./runtimeWiring", async (importOriginal) => ({ ...await importOriginal<typeof import("./runtimeWiring")>(), createRuntimeWatch: () => ({ dispose: () => undefined }) }));
vi.mock("./stationScene", async (importOriginal) => {
  const { StationScene } = await importOriginal<typeof import("./stationScene")>();
  return { StationScene: class extends StationScene { override updateLabels() {} override render() {} } };
});

const original = useMapStore.getState();
afterEach(() => { useMapStore.setState(original, true); calls.length = 0; vi.useRealTimers(); });

it.each([false, true])("initializes the map camera before attaching the overlay (saved=%s)", (saved) => {
  useMapStore.setState({ cameraSnapshot: saved ? { center: [-97, 35], zoom: 4 } : null });
  const runtime = new MapRuntime(document.createElement("div"), 1);
  try {
    expect(calls).toEqual([saved ? "saved camera" : "default camera", "overlay attached"]);
  } finally {
    runtime.dispose();
  }
});

it.each([false, true])("restores the exact latest camera when rebuilt before the snapshot delay (move ended=%s)", (ended) => {
  vi.useFakeTimers();
  const previous = { center: [-98, 36] as [number, number], zoom: 4 };
  const latest = { center: [-97.4567890123, 35.2345678901] as [number, number], zoom: 6.375 };
  useMapStore.setState({ cameraSnapshot: previous });
  const runtime = new MapRuntime(document.createElement("div"));
  runtime.map?.jumpTo(latest);
  if (ended) runtime.moveEnded();
  runtime.dispose();
  expect(useMapStore.getState().cameraSnapshot).toEqual(latest);
  const replacement = new MapRuntime(document.createElement("div"));
  try {
    expect(replacement.map?.getCenter()).toEqual({ lng: latest.center[0], lat: latest.center[1] });
    expect(replacement.map?.getZoom()).toBe(latest.zoom);
    vi.advanceTimersByTime(500);
    expect(useMapStore.getState().cameraSnapshot).toEqual(latest);
  } finally { replacement.dispose(); }
});

it("does not replace the shared camera when a secondary panel closes", () => {
  const snapshot = { center: [-97, 35] as [number, number], zoom: 6.375 };
  useMapStore.setState({ cameraSnapshot: snapshot });
  const runtime = new MapRuntime(document.createElement("div"), 1);
  runtime.map?.jumpTo({ center: [-90, 40], zoom: 3 });
  runtime.dispose();
  expect(useMapStore.getState().cameraSnapshot).toBe(snapshot);
});

it("focuses radar coordinates without a sounding-station record and retains a closer zoom", () => {
  const runtime = new MapRuntime(document.createElement("div"));
  const secondary = new MapRuntime(document.createElement("div"), 1);
  const position = { latitude: 35.3333, longitude: -97.2783 };
  try {
    useMapStore.getState().requestFocus("KTLX", position);
    expect(runtime.map?.getCenter()).toEqual({ lat: position.latitude, lng: position.longitude });
    expect(runtime.map?.getZoom()).toBe(5);
    expect(secondary.map?.getCenter()).toEqual({ lat: 35, lng: -97 });
    runtime.map?.jumpTo({ center: [-90, 30], zoom: 7.25 });
    useMapStore.getState().requestFocus("KTLX", position);
    expect(runtime.map?.getCenter()).toEqual({ lat: position.latitude, lng: position.longitude });
    expect(runtime.map?.getZoom()).toBe(7.25);
  } finally { runtime.dispose(); secondary.dispose(); }
});

it("renders changed radar selection and availability scene data immediately", () => {
  const runtime = new MapRuntime(document.createElement("div"), 1);
  if (runtime.scene === null) throw new Error("Missing station scene");
  const render = vi.spyOn(runtime.scene, "render"), updateLabels = vi.spyOn(runtime.scene, "updateLabels"), setData = vi.spyOn(runtime.scene, "setData");
  try {
    const unavailableRadarSites = new Set(["KTLX"]);
    runtime.setScene({ selectedRadarSite: "KTLX" });
    expect(updateLabels).toHaveBeenCalledTimes(1);
    runtime.setScene({ unavailableRadarSites });
    expect(setData).toHaveBeenNthCalledWith(1, { selectedRadarSite: "KTLX" });
    expect(setData).toHaveBeenNthCalledWith(2, { unavailableRadarSites });
    expect(render).toHaveBeenCalledTimes(2);
    expect(updateLabels).toHaveBeenCalledTimes(1);
    runtime.setScene({ selectedRadarSite: null });
    expect(updateLabels).toHaveBeenCalledTimes(2);
  } finally { runtime.dispose(); }
});

it("updates capture readiness without submitting layers again when the scene stays the same", () => {
  const runtime = new MapRuntime(document.createElement("div"), 1);
  try {
    const { scene } = runtime;
    if (scene === null) throw new Error("Missing station scene");
    const radarSites = [{ id: "KTLX", latitude: 35.33, longitude: -97.28 }];
    runtime.setScene({ radarSites, captureTime: "2026-11-20T09:00:00Z" });
    const render = vi.spyOn(scene, "render");
    const updateLabels = vi.spyOn(scene, "updateLabels");

    runtime.setScene({ radarSites, captureTime: "2026-11-20T10:00:00Z", captureReady: true });
    expect(scene.readyForCapture()).toBe(true);
    expect(render).not.toHaveBeenCalled();

    runtime.setScene({ radarSites, captureReady: false, reports: [] });
    expect(scene.readyForCapture()).toBe(false);
    expect(render).toHaveBeenCalledTimes(1);
    expect(updateLabels).not.toHaveBeenCalled();
  } finally { runtime.dispose(); }
});

it("clears a removed spotter tooltip for its own panel while preserving other inspection state", () => {
  const runtime = new MapRuntime(document.createElement("div"), 1);
  const other = new MapRuntime(document.createElement("div"), 2);
  const position = { longitude: -97, latitude: 35, updatedAt: "2026-11-20T09:10:27Z", chaseCam: false };
  const remaining = { ...position, longitude: -96 };
  const report = {
    label: "Active spotter", description: "Updated 2026-11-20 09:10:27 UTC", caption: "", x: 120, y: 60,
    source: { kind: "spotter", panel: 1, position },
  } satisfies HoveredReport;
  const pointer = { panel: 1, longitude: -97, latitude: 35, x: 100, y: 50 };
  const pinnedSpotter = { panel: 1, position, x: 120, y: 60 };
  const stormReport = { label: "Hail", description: "", caption: "", x: 120, y: 60 };
  const { setReport } = useHoverStore.getState();
  try {
    runtime.setScene({ spotterPositions: [position, remaining] });
    useHoverStore.setState({ report, pointer, pinnedSpotter });
    other.setScene({ spotterPositions: null });
    runtime.setScene({ reports: [] });
    runtime.setScene({ spotterPositions: [remaining, { ...position }] });
    expect(useHoverStore.getState().report).toBe(report);
    expect(useHoverStore.getState().pinnedSpotter).toBe(pinnedSpotter);

    runtime.setScene({ spotterPositions: [remaining] });
    expect(useHoverStore.getState().report).toBeNull();
    expect(useHoverStore.getState().pinnedSpotter).toBeNull();
    expect(useHoverStore.getState().pointer).toBe(pointer);

    runtime.setScene({ spotterPositions: [position] });
    setReport(report);
    runtime.setScene({ spotterPositions: null });
    expect(useHoverStore.getState().report).toBeNull();

    setReport(stormReport);
    runtime.setScene({ spotterPositions: [] });
    expect(useHoverStore.getState().report).toBe(stormReport);
  } finally { runtime.dispose(); other.dispose(); }
});

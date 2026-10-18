// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { useMapStore } from "../../stores/mapStore";
import { MapRuntime } from "./MapRuntime";

const calls = vi.hoisted(() => [] as string[]);
vi.mock("./maplibreWorker", () => ({}));
vi.mock("maplibre-gl", () => ({ Map: class {
  setPadding() {}
  jumpTo() { calls.push("saved camera"); }
  fitBounds() { calls.push("default camera"); }
  addControl() { calls.push("overlay attached"); }
  remove() {}
} }));
vi.mock("../canvasTokens", () => ({ documentTokenReader: () => () => "monospace", readCanvasTokens: () => ({ lengths: { pickingRadius: 6 } }) }));
vi.mock("./mapOptions", () => ({ mapOptions: () => ({}), panelPixelRatio: () => 1 }));
vi.mock("./runtimeEvents", () => ({ wireMapEvents: () => undefined, createPanelOverlay: () => ({}), wireOverlayEvents: () => () => undefined }));
vi.mock("./cameraLink", () => ({ linkCamera: () => () => undefined }));
vi.mock("./runtimeWiring", () => ({ NO_WATCH: { dispose: () => undefined }, subscribeToStores: () => [] }));
vi.mock("./stationScene", () => ({ StationScene: class { updateLabels() {} render() {} } }));

const original = useMapStore.getState();
afterEach(() => { useMapStore.setState(original, true); calls.length = 0; });

it.each([false, true])("initializes the map camera before attaching the overlay (saved=%s)", (saved) => {
  useMapStore.setState({ cameraSnapshot: saved ? { center: [-97, 35], zoom: 4 } : null });
  const runtime = new MapRuntime(document.createElement("div"), 1);
  try {
    expect(calls).toEqual([saved ? "saved camera" : "default camera", "overlay attached"]);
  } finally {
    runtime.dispose();
  }
});

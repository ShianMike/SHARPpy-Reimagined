// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MapOverlaySlotsProvider } from "../app/MapOverlaySlots";
import { useMapStore, type PanelLayout } from "../stores/mapStore";
import type { MapPanelProps } from "./panels/MapPanel";
import type { MapRuntime } from "./runtime/MapRuntime";
import WeatherMap from "./WeatherMap";

const { mounts } = vi.hoisted(() => ({ mounts: vi.fn() }));
vi.mock("../stations/stationQueries", () => ({ useStationCatalog: () => ({ data: { stations: [] } }) }));
vi.mock("./mapQueries", () => ({ useStationAvailability: () => ({ data: undefined }) }));
vi.mock("./usePlayback", () => ({ usePlayback: () => undefined }));
vi.mock("./panels/MapPanel", async () => {
  const { useEffect } = await import("react");
  return { MAP_HINT_ID: "map-hint", MapPanel({ panel, onRuntime }: MapPanelProps) {
    useEffect(() => {
      mounts(panel);
      onRuntime?.({ zoomBy: vi.fn(), resetView: vi.fn() } as unknown as MapRuntime);
      return () => { onRuntime?.(null); };
    }, [panel, onRuntime]);
    return <div data-testid={`map-panel-${String(panel + 1)}`} />;
  } };
});

const initial = useMapStore.getState();
const pending: (() => void)[] = [];
const startTransition = vi.fn((update: () => void) => { pending.push(update); return { ready: Promise.resolve() }; });
const getAnimations = vi.fn(() => []);
let left: HTMLDivElement;
beforeEach(() => {
  useMapStore.setState(initial, true);
  pending.length = 0; startTransition.mockClear(); mounts.mockClear(); getAnimations.mockClear();
  Object.defineProperty(HTMLDivElement.prototype, "startViewTransition", { configurable: true, value: startTransition });
  Object.defineProperty(HTMLDivElement.prototype, "getAnimations", { configurable: true, value: getAnimations });
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  left = document.createElement("div"); document.body.append(left);
});
afterEach(() => {
  cleanup(); left.remove();
  Reflect.deleteProperty(HTMLDivElement.prototype, "startViewTransition");
  Reflect.deleteProperty(HTMLDivElement.prototype, "getAnimations");
  vi.unstubAllGlobals();
});

function openMap(): void {
  render(<MapOverlaySlotsProvider slots={{ left, centre: null, rightEnd: null }}><WeatherMap /></MapOverlaySlotsProvider>);
}
const labels: Record<PanelLayout, string> = { 1: "One map", 2: "Two synchronized panels", 4: "Four synchronized panels" };
function choose(layout: PanelLayout): void { fireEvent.click(screen.getByRole("button", { name: labels[layout] })); }

it("crossfades layout changes without replacing surviving maps or changing their settings", () => {
  useMapStore.setState({ cameraSnapshot: { center: [-97, 35], zoom: 6 } });
  const saved = useMapStore.getState();
  openMap();
  const primary = screen.getByTestId("map-panel-1");
  for (const layout of [2, 4, 2, 1] as const) {
    const before = useMapStore.getState().panelLayout;
    choose(layout);
    expect(useMapStore.getState().panelLayout).toBe(before);
    act(() => { pending.shift()?.(); });
    expect(useMapStore.getState().panelLayout).toBe(layout);
    expect(screen.getAllByTestId(/map-panel-/)).toHaveLength(layout);
    expect(screen.getByTestId("map-panel-1")).toBe(primary);
  }
  expect(startTransition).toHaveBeenCalledTimes(4);
  expect(mounts.mock.calls.filter(([panel]) => panel === 0)).toHaveLength(1);
  expect(useMapStore.getState().cameraSnapshot).toBe(saved.cameraSnapshot);
  expect(useMapStore.getState().layers).toBe(saved.layers);
  expect(useMapStore.getState().panels).toBe(saved.panels);
});

it.each(["reduced motion", "unsupported browser"])("changes layout immediately with %s", (condition) => {
  if (condition === "reduced motion") vi.stubGlobal("matchMedia", () => ({ matches: true }));
  else Reflect.deleteProperty(HTMLDivElement.prototype, "startViewTransition");
  openMap(); choose(4);
  expect(useMapStore.getState().panelLayout).toBe(4);
  expect(screen.getAllByTestId(/map-panel-/)).toHaveLength(4);
  expect(startTransition).not.toHaveBeenCalled();
  const grid = screen.getByTestId("map-panel-1").parentElement;
  expect(grid?.dataset.motion).toBe(condition === "unsupported browser" ? "fade" : undefined);
  choose(2);
  expect(screen.getAllByTestId(/map-panel-/)).toHaveLength(2);
  expect(getAnimations).toHaveBeenCalledTimes(condition === "unsupported browser" ? 2 : 0);
});

it("applies only the latest click when transitions are skipped during rapid switching", async () => {
  startTransition.mockImplementationOnce((update) => {
    pending.push(update);
    return { ready: Promise.reject(new DOMException("Transition skipped", "AbortError")) };
  });
  openMap();
  const primary = screen.getByTestId("map-panel-1");
  choose(2); choose(4); choose(1);
  await act(async () => { pending.forEach((update) => { update(); }); await Promise.resolve(); });
  expect(startTransition).toHaveBeenCalledTimes(3);
  expect(useMapStore.getState().panelLayout).toBe(1);
  expect(screen.getAllByTestId(/map-panel-/)).toEqual([primary]);
  expect(mounts).toHaveBeenCalledTimes(1);
});

it("does not animate when the selected layout is clicked again", () => {
  openMap(); choose(1);
  expect(startTransition).not.toHaveBeenCalled();
});

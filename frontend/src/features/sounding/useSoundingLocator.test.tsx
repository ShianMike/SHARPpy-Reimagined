// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { registerSoundingMapContext, type SoundingMapContext, type SoundingMapSnapshot } from "../../map/runtime/soundingLocator";
import type { SoundingSummary } from "../../types/soundings";
import type { SoundingView } from "../../types/soundingView";
import { useSoundingStore } from "../../stores/soundingStore";
import { useMapStore } from "../../stores/mapStore";
import { useSoundingLocator } from "./useSoundingLocator";

const fixture = vi.hoisted(() => {
  const request = { model: "hrrr", product: "tmp", runTime: "2026-11-15T00:00:00Z", forecastHour: 2 };
  const frame = { ...request, validTime: "2026-11-15T02:00:00Z", attribution: "NOAA" };
  return { request, frame, capture: vi.fn(), compose: vi.fn() };
});
vi.mock("../../map/runtime/soundingLocator", async (importActual) => ({
  ...(await importActual<typeof import("../../map/runtime/soundingLocator")>()), captureSoundingMap: fixture.capture,
}));
vi.mock("./soundingLocatorImage", () => ({ composeSoundingLocator: fixture.compose }));

const summary = { soundingId: "test", kind: "model", model: "HRRR", provider: "hrrr", runTime: fixture.request.runTime,
  validTime: fixture.frame.validTime, forecastHour: 2, latitude: 35, longitude: -97 } as SoundingSummary;
const locator = { image: new Blob(["map"], { type: "image/png" }), point: { x: 0.5, y: 0.5 },
  basemapAttribution: "OpenStreetMap" } as SoundingMapSnapshot;
const product: SoundingMapContext["product"] = { key: "tmp", label: "Temperature", shortLabel: "Temp", category: "Surface",
  description: "", caveat: "", minimumForecastHour: 0, contour: null, wind: false,
  palette: { units: "°C", display: "auto", stepped: false, floor: null, ceiling: null, ticks: [], stops: [[-20, "#0000ff"], [20, "#ff0000"]] } };
const view = { image: new Blob(["original"], { type: "image/png" }), width: 3000, height: 2000,
  locatorBounds: { left: 0.55, top: 0.01, right: 0.7, bottom: 0.12, location: "Norman, Oklahoma" } } as SoundingView;
let queryClient: QueryClient;
let unregisterContext: () => void;
beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  useMapStore.setState(useMapStore.getInitialState(), true);
  useSoundingStore.getState().add(summary);
  unregisterContext = registerSoundingMapContext({ panel: 0, request: fixture.request, product,
    model: "HRRR", frame: fixture.frame });
  fixture.capture.mockResolvedValue(locator);
  fixture.compose.mockImplementation((source: SoundingView) => Promise.resolve({ ...source, image: new Blob(["composed"], { type: "image/png" }) }));
});
afterEach(() => { cleanup(); unregisterContext(); queryClient.clear(); vi.clearAllMocks(); });

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

it("replaces the displayed and exported image only after the matching map is composed", async () => {
  const hook = renderHook(() => useSoundingLocator("test", 0, view), { wrapper });
  expect(hook.result.current.view).toBe(view);
  expect(hook.result.current.pending).toBe(true);
  await waitFor(() => { expect(hook.result.current.view?.image).not.toBe(view.image); });
  expect(fixture.capture).toHaveBeenCalledOnce();
  expect(fixture.compose).toHaveBeenCalledWith(view, locator, expect.anything(), "Norman, Oklahoma");
  expect(hook.result.current.view?.width).toBe(view.width);
  expect(hook.result.current.pending).toBe(false);
  hook.unmount();
});

it("never attaches an earlier model run to a sounding", async () => {
  useSoundingStore.setState({ open: [{ ...summary, runTime: "2026-11-14T00:00:00Z" }] });
  const hook = renderHook(() => useSoundingLocator("test", 0, view), { wrapper });
  await new Promise((resolve) => { setTimeout(resolve, 0); });
  expect(hook.result.current.view).toBe(view);
  expect(fixture.capture).not.toHaveBeenCalled();
  hook.unmount();
});

it("renders a fresh locator for a higher resolution instead of reusing the small capture", async () => {
  const hook = renderHook(({ source }) => useSoundingLocator("test", 0, source), { wrapper, initialProps: { source: view } });
  await waitFor(() => { expect(hook.result.current.pending).toBe(false); });
  expect(fixture.capture).toHaveBeenLastCalledWith(expect.anything(), 35, -97, { width: 450, height: 220 }, expect.any(AbortSignal));
  const larger = { ...view, width: 4200, height: 2800 };
  hook.rerender({ source: larger });
  expect(hook.result.current.view).toBe(larger);
  await waitFor(() => { expect(hook.result.current.pending).toBe(false); });
  expect(fixture.capture).toHaveBeenLastCalledWith(expect.anything(), 35, -97, { width: 630, height: 308 }, expect.any(AbortSignal));
  expect(hook.result.current.view?.image).not.toBe(larger.image);
});

it("omits the locator while the active map is unavailable", async () => {
  useMapStore.setState({ view: "live" });
  const hook = renderHook(() => useSoundingLocator("test", 0, view), { wrapper });
  await new Promise((resolve) => { setTimeout(resolve, 0); });
  expect(hook.result.current.view).toBe(view);
  expect(fixture.capture).not.toHaveBeenCalled();
  hook.unmount();
});

it("refreshes the snapshot if a panel publishes a replacement for the same forecast", async () => {
  const hook = renderHook(() => useSoundingLocator("test", 0, view), { wrapper });
  await waitFor(() => { expect(fixture.compose).toHaveBeenCalledOnce(); });
  let unregisterReplacement: () => void = () => undefined;
  act(() => { unregisterReplacement = registerSoundingMapContext({ panel: 0, request: fixture.request,
    model: "HRRR", product, frame: fixture.frame }); });
  await waitFor(() => { expect(fixture.capture).toHaveBeenCalledTimes(2); });
  hook.unmount(); unregisterReplacement();
});

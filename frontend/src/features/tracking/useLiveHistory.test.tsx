// @vitest-environment jsdom
import radar from "@contracts/public/map/radar.shrf?url&inline";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { LIVE_MINUTE, liveHistoryTimes, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { renderWithBackend } from "../../test/backendHarness";
import { LiveTimeline } from "./LiveTimeline";
import { isLiveQuery } from "./liveRequests";

const map = useMapStore.getState(), app = useAppStore.getState(), clock = useLiveTrackingStore.getState(), ui = useUiStore.getState();
const now = Date.parse("2026-10-17T08:00:00Z");
const forecast = useForecastStore.getState();
const frame = () => Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer;
beforeEach(() => {
  useMapStore.setState(map, true); useAppStore.setState(app, true); useUiStore.setState({ ...ui, speed: 1, loop: false }, true);
  useLiveTrackingStore.setState({ ...clock, now, selectedTime: null }, true);
  useAppStore.getState().setActiveWorkspace("live");
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 2, height: 2 }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("caches thirteen full-resolution steps, scrubs without changing the forecast, and resumes live", async () => {
  vi.spyOn(Date, "now").mockReturnValue(now);
  const transport = createTransport(), original = transport.call.bind(transport);
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame" ? Promise.resolve(frame()) : original(command, args));
  const { queryClient, view } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Cache past hour" }));
  await waitFor(() => { expect(screen.getByRole("status", { name: "Live data status" }).textContent).toContain("13 cached"); });
  const radarCalls = call.mock.calls.filter(([command]) => command === "get_radar_frame");
  expect(radarCalls).toHaveLength(13);
  const times = radarCalls.map(([, args]) => Date.parse(String(args?.validTime)));
  expect(Math.max(...times) - Math.min(...times)).toBe(60 * LIVE_MINUTE);
  expect(queryClient.getQueryCache().findAll({ predicate: (query) => isLiveQuery(query.queryKey) })).toHaveLength(13);
  fireEvent.change(screen.getByRole("slider", { name: "Live history" }), { target: { value: "-30" } });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(useLiveTrackingStore.getState().now - 30 * LIVE_MINUTE);
  expect(screen.getByLabelText("Live viewing time").textContent).toContain(":30Z");
  expect(useForecastStore.getState()).toEqual(forecast);
  fireEvent.click(screen.getByRole("button", { name: "Live" }));
  expect(useLiveTrackingStore.getState().selectedTime).toBeNull();
  view.unmount(); queryClient.clear();
});

it("retains the whole compressed hour across four matching panels and plays it without another download", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useMapStore.getState().setPanelLayout(4);
  const transport = createTransport(), call = transport.call.bind(transport);
  const fetch = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame" ? Promise.resolve(frame()) : call(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Cache past hour" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByRole("status", { name: "Live data status" }).textContent).toBe("13 cached");
  expect(screen.getByRole("button", { name: "Cache past hour" }).textContent).toContain("Ready");
  const product = useMapStore.getState().radarProduct;
  act(() => { useMapStore.getState().setLayerOpacity("radar", 0.4); });
  expect(screen.getByRole("button", { name: "Cache past hour" }).textContent).toContain("Ready");
  act(() => { useMapStore.getState().setRadarProduct("echo-tops"); });
  expect(screen.getByRole("button", { name: "Cache past hour" }).textContent).not.toContain("Ready");
  act(() => { useMapStore.getState().setRadarProduct(product); });
  expect(screen.getByRole("button", { name: "Cache past hour" }).textContent).toContain("Ready");
  expect(queryClient.getQueryCache().findAll({ predicate: (query) => isLiveQuery(query.queryKey) })).toHaveLength(13);
  expect(fetch.mock.calls.filter(([command]) => command === "get_radar_frame")).toHaveLength(13);
  fetch.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(751); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 55 * LIVE_MINUTE);
  expect(fetch.mock.calls.filter(([command]) => command === "get_radar_frame")).toHaveLength(0);
  expect(createImageBitmap).toHaveBeenCalled();
  view.unmount(); queryClient.clear();
});

it("reuses downloaded five-minute steps after the minute clock advances", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now + LIVE_MINUTE);
  const transport = createTransport(), call = transport.call.bind(transport);
  const fetch = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame" ? Promise.resolve(frame()) : call(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Cache past hour" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByRole("status", { name: "Live data status" }).textContent).toBe("13 cached");
  fetch.mockClear();
  act(() => { useLiveTrackingStore.getState().tick(now + 2 * LIVE_MINUTE); });
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(751); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 50 * LIVE_MINUTE);
  expect(fetch.mock.calls.filter(([command]) => command === "get_radar_frame")).toHaveLength(0);
  expect(liveHistoryTimes(now + 2 * LIVE_MINUTE)).toEqual([...liveHistoryTimes(now + LIVE_MINUTE).slice(0, -1), now + 2 * LIVE_MINUTE]);
  view.unmount(); queryClient.clear();
});

it("continues downloading behind a slow frame, reports each completion, and retries only a failed frame", async () => {
  let release: (data: ArrayBuffer) => void = () => undefined;
  const slow = new Promise<ArrayBuffer>((resolve) => { release = resolve; });
  const transport = createTransport(), call = transport.call.bind(transport);
  let count = 0, pending = 0, peak = 0;
  vi.spyOn(transport, "call").mockImplementation((command, args) => {
    if (command !== "get_radar_frame") return call(command, args);
    const index = count++;
    peak = Math.max(peak, ++pending);
    return (index === 0 ? slow : index === 5 ? Promise.reject(new Error("Provider timeout")) : Promise.resolve(frame())).finally(() => { pending--; });
  });
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Cache past hour" }));
  await waitFor(() => { expect(screen.getByRole("status", { name: "Live data status" }).textContent).toBe("Caching 12 / 13"); });
  expect(screen.getByRole("button", { name: "Stop caching" }).textContent).toContain("Loading");
  expect(count).toBe(13); expect(peak).toBe(2);
  await act(async () => { release(frame()); await slow; });
  await waitFor(() => { expect(screen.getByRole("status", { name: "Live data status" }).textContent).toBe("12 cached · 1 unavailable"); });
  expect(screen.getByRole("button", { name: "Cache past hour" }).textContent).toContain("Retry");
  fireEvent.click(screen.getByRole("button", { name: "Cache past hour" }));
  await waitFor(() => { expect(screen.getByRole("status", { name: "Live data status" }).textContent).toBe("13 cached"); });
  expect(count).toBe(14);
  view.unmount(); queryClient.clear();
});

it("waits for PNG decoding before advancing the playback time", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  let ready: (image: { width: number; height: number }) => void = () => undefined;
  vi.stubGlobal("createImageBitmap", vi.fn().mockImplementation(() => new Promise((resolve) => { ready = resolve; })));
  const transport = createTransport(), call = transport.call.bind(transport);
  vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame" ? Promise.resolve(frame()) : call(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(751); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 60 * LIVE_MINUTE);
  await act(async () => { ready({ width: 2, height: 2 }); await Promise.resolve(); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 55 * LIVE_MINUTE);
  view.unmount(); queryClient.clear();
});

it.each([false, true])("stops queuing downloads while allowing in-flight frames to finish (source change: %s)", async (changeSources) => {
  const releases: ((data: ArrayBuffer) => void)[] = [];
  const transport = createTransport(), call = transport.call.bind(transport);
  let count = 0;
  vi.spyOn(transport, "call").mockImplementation((command, args) => {
    if (command !== "get_radar_frame") return call(command, args);
    count++;
    return new Promise<ArrayBuffer>((resolve) => { releases.push(resolve); });
  });
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Cache past hour" }));
  await waitFor(() => { expect(count).toBe(2); });
  if (changeSources) {
    const product = useMapStore.getState().radarProduct;
    act(() => { useMapStore.getState().setRadarProduct("echo-tops"); });
    act(() => { useMapStore.getState().setRadarProduct(product); });
  } else fireEvent.click(screen.getByRole("button", { name: "Stop caching" }));
  await act(async () => { for (const release of releases) release(frame()); await Promise.resolve(); });
  expect(count).toBe(2);
  expect(screen.getByRole("status", { name: "Live data status" }).textContent).toBe("Caching stopped");
  expect(screen.getByRole("button", { name: "Cache past hour" }).textContent).toContain("Resume");
  view.unmount(); queryClient.clear();
});

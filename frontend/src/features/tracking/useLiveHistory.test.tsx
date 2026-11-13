// @vitest-environment jsdom
import radar from "@contracts/public/map/radar.shrf?url&inline";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { encodeShrf, parseShrf } from "../../lib/contracts/frames";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { LIVE_MINUTE, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { renderWithBackend } from "../../test/backendHarness";
import { LiveTimeline } from "./LiveTimeline";

const map = useMapStore.getState(), app = useAppStore.getState(), clock = useLiveTrackingStore.getState(), ui = useUiStore.getState();
const now = Date.parse("2026-10-17T08:00:00Z");
const forecast = useForecastStore.getState();
const raster = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
const frame = (time: number) => encodeShrf({ ...raster.header, validTime: liveTimestamp(time) }, raster.body);

beforeEach(() => {
  useMapStore.setState(map, true); useAppStore.setState(app, true); useUiStore.setState({ ...ui, speed: 1, loop: false }, true);
  useLiveTrackingStore.setState({ ...clock, now, selectedTime: null, playing: false }, true);
  useAppStore.getState().setActiveWorkspace("live");
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 2, height: 2 }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("does not fetch other radar frames or tilts while live playback is paused", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useMapStore.getState().setRadarScope("site"); useMapStore.getState().setRadarSite("KTLX");
  const transport = createTransport(), call = vi.spyOn(transport, "call");
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  act(() => { useLiveTrackingStore.getState().tick(now + LIVE_MINUTE); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(call.mock.calls.filter(([command]) => command === "get_radar_site_frame" || command === "get_radar_tilts")).toHaveLength(0);
  view.unmount(); queryClient.clear();
});

it("scrubs the live clock without changing the forecast and returns to live", () => {
  vi.spyOn(Date, "now").mockReturnValue(now);
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, createTransport());
  fireEvent.change(screen.getByRole("slider", { name: "Live history" }), { target: { value: "-30" } });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 30 * LIVE_MINUTE);
  expect(screen.getByLabelText("Live viewing time").textContent).toContain(":30Z");
  expect(useForecastStore.getState()).toEqual(forecast);
  fireEvent.click(screen.getByRole("button", { name: "Live" }));
  expect(useLiveTrackingStore.getState().selectedTime).toBeNull();
  view.unmount(); queryClient.clear();
});

it("updates the live clock at the UTC minute boundary", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now + 59_000);
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, createTransport());
  expect(useLiveTrackingStore.getState().now).toBe(now);
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(useLiveTrackingStore.getState().now).toBe(now + LIVE_MINUTE);
  view.unmount(); queryClient.clear();
});

it("loads only the next playback time and waits for its image before advancing", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  let ready: (image: { width: number; height: number }) => void = () => undefined;
  vi.stubGlobal("createImageBitmap", vi.fn().mockImplementation(() => new Promise((resolve) => { ready = resolve; })));
  const transport = createTransport(), original = transport.call.bind(transport);
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame"
    ? Promise.resolve(frame(Date.parse(String(args?.validTime)))) : original(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(751); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 60 * LIVE_MINUTE);
  const radarCalls = call.mock.calls.filter(([command]) => command === "get_radar_frame");
  expect(radarCalls).toHaveLength(1);
  expect(radarCalls[0]?.[1]?.validTime).toBe(liveTimestamp(now - 55 * LIVE_MINUTE));
  await act(async () => { ready({ width: 2, height: 2 }); await Promise.resolve(); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 55 * LIVE_MINUTE);
  view.unmount(); queryClient.clear();
});

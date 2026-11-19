// @vitest-environment jsdom
import { act, fireEvent, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapKeys } from "../../lib/backend/queryOptions";
import { useForecastStore } from "../../stores/forecastStore";
import { LIVE_MINUTE, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { renderWithBackend } from "../../test/backendHarness";
import { LiveTimeline } from "./LiveTimeline";
import { frame, now, setupLivePlaybackTests } from "./livePlaybackTestSetup";
import { useStormReports } from "../../map/mapQueries";
import { FRAME_INTERVAL_MS } from "../../map/usePlayback";
import { useWeatherAlerts } from "../../map/weatherAlertsQuery";
import { isLiveQuery } from "./liveRequests";
import { useWeatherTime } from "./useWeatherTime";

setupLivePlaybackTests();
const forecast = useForecastStore.getState();

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
  useLiveTrackingStore.getState().selectTime(now - 60 * LIVE_MINUTE);
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

it.each(["start", "finish"] as const)("keeps the current live scene while playback prepares its %s frame", async (step) => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  const current = step === "start" ? null : now - 5 * LIVE_MINUTE;
  const target = step === "start" ? now - 60 * LIVE_MINUTE : now;
  useLiveTrackingStore.getState().selectTime(current);
  let ready: (image: { width: number; height: number }) => void = () => undefined;
  vi.stubGlobal("createImageBitmap", vi.fn().mockImplementation(() => new Promise((resolve) => { ready = resolve; })));
  const transport = createTransport(), original = transport.call.bind(transport);
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) =>
    command === "get_radar_frame" ? Promise.resolve(frame(target)) : original(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  act(() => { useMapStore.getState().setLayerOpacity("radar", 0.5); });
  await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(current);
  expect(screen.getByLabelText("Live viewing time").getAttribute("aria-busy")).toBe("true");
  const calls = call.mock.calls.filter(([command]) => command === "get_radar_frame");
  expect(calls).toHaveLength(1);
  expect(calls[0]?.[1]?.validTime).toBe(step === "start" ? liveTimestamp(target) : undefined);
  await act(async () => { ready({ width: 2, height: 2 }); await vi.advanceTimersByTimeAsync(0); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(step === "start" ? target : null);
  expect(useLiveTrackingStore.getState().playing).toBe(step === "start");
  view.unmount(); queryClient.clear();
});

it("keeps the old time until reports settle and shows a missing alert history status at the new time", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const previous = now - 60 * LIVE_MINUTE, next = now - 55 * LIVE_MINUTE;
  useLiveTrackingStore.getState().selectTime(previous);
  useMapStore.getState().setLayerVisible("radar", false);
  useMapStore.getState().setLayerVisible("warnings", true);
  useMapStore.getState().setLayerVisible("storm-reports", true);
  let finishReport: () => void = () => undefined;
  const transport = createTransport(), original = transport.call.bind(transport);
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => {
    if (command === "get_weather_alerts" && args?.validTime === liveTimestamp(next)) {
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- public backend error contract
      return Promise.reject({ code: "OVERLAY_FETCH_FAILED", message: "No session snapshot", retryable: false, details: { reason: "historyUnavailable" } });
    }
    if (command === "get_storm_reports" && args?.validTime === liveTimestamp(next)) {
      const reply = original(command, args);
      return new Promise((resolve) => { finishReport = () => { void reply.then(resolve); }; });
    }
    return original(command, args);
  });
  function WeatherProbe() {
    const time = useWeatherTime();
    const alerts = useWeatherAlerts(time, true), reports = useStormReports(time, 21600, "all", true);
    return <><output data-testid="alerts-time">{alerts.isError ? "history unavailable" : alerts.data?.validTime ?? "empty"}</output>
      <output data-testid="reports-time">{reports.isError ? "unavailable" : reports.data?.validTime ?? "empty"}</output></>;
  }
  const { view, queryClient } = renderWithBackend(<><WeatherProbe /><LiveTimeline /></>, transport);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByTestId("alerts-time").textContent).toBe(liveTimestamp(previous));
  expect(screen.getByTestId("reports-time").textContent).toBe(liveTimestamp(previous));
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(previous);
  expect(screen.getByTestId("alerts-time").textContent).toBe(liveTimestamp(previous));
  expect(screen.getByTestId("reports-time").textContent).toBe(liveTimestamp(previous));
  await act(async () => { finishReport(); await vi.advanceTimersByTimeAsync(0); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(next);
  expect(screen.getByTestId("alerts-time").textContent).toBe("history unavailable");
  expect(screen.getByTestId("reports-time").textContent).toBe(liveTimestamp(next));
  expect(screen.getByLabelText("Live data status").textContent).toContain("layers unavailable");
  expect(call.mock.calls.filter(([command, args]) => command === "get_weather_alerts" && args?.validTime === liveTimestamp(next))).toHaveLength(1);
  view.unmount(); queryClient.clear();
});

it("trims a cancelled preparation after it finishes without changing the user's selection", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useLiveTrackingStore.getState().selectTime(now - 60 * LIVE_MINUTE);
  let finish: (value: ArrayBuffer) => void = () => undefined;
  const transport = createTransport(), original = transport.call.bind(transport);
  vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame"
    ? new Promise((resolve) => { finish = resolve; }) : original(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  for (let index = 0; index < 128; index++) {
    queryClient.setQueryData(mapKeys.satellite("visible", liveTimestamp(now - (index + 1) * LIVE_MINUTE)),
      { png: new Blob(["cached"]) }, { updatedAt: now - index });
  }
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  fireEvent.change(screen.getByRole("slider", { name: "Live history" }), { target: { value: "-20" } });
  await act(async () => { finish(frame(now - 55 * LIVE_MINUTE)); await vi.advanceTimersByTimeAsync(0); });
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: now - 20 * LIVE_MINUTE, playing: false });
  expect(queryClient.getQueryCache().findAll({ predicate: (query) => isLiveQuery(query.queryKey) }).length).toBeLessThanOrEqual(128);
  view.unmount(); queryClient.clear();
});

it("ignores a superseded report filter while waiting for the new panel selection", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  const first = now - 60 * LIVE_MINUTE, next = now - 55 * LIVE_MINUTE;
  useLiveTrackingStore.getState().selectTime(first);
  useMapStore.getState().setLayerVisible("radar", false);
  useMapStore.getState().setLayerVisible("storm-reports", true);
  const finish = new Map<string, () => void>();
  const transport = createTransport(), original = transport.call.bind(transport);
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => {
    if (command !== "get_storm_reports") return original(command, args);
    const reply = original(command, args);
    return new Promise((resolve) => { finish.set(String(args?.hazardFilter), () => { void reply.then(resolve); }); });
  });
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  act(() => { useMapStore.getState().setReportFilter("tornado"); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(call.mock.calls.filter(([command]) => command === "get_storm_reports").map(([, args]) => args?.hazardFilter)).toEqual(["all", "tornado"]);
  await act(async () => { finish.get("all")?.(); await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(first);
  await act(async () => { finish.get("tornado")?.(); await vi.advanceTimersByTimeAsync(0); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(next);
  expect(queryClient.getQueryData(mapKeys.reports(liveTimestamp(next), 21600, "tornado"))).toBeDefined();
  view.unmount(); queryClient.clear();
});

it("does not advance a prepared frame when its report filter changes before effect cleanup", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  const first = now - 60 * LIVE_MINUTE;
  useLiveTrackingStore.getState().selectTime(first);
  useMapStore.getState().setLayerVisible("radar", false);
  useMapStore.getState().setLayerVisible("storm-reports", true);
  const transport = createTransport(), original = transport.call.bind(transport);
  vi.spyOn(transport, "call").mockImplementation((command, args) => {
    if (command === "get_storm_reports" && args?.hazardFilter !== "all") return new Promise(() => undefined);
    return original(command, args);
  });
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(queryClient.getQueryData(mapKeys.reports(liveTimestamp(now - 55 * LIVE_MINUTE), 21600, "all"))).toBeDefined();
  await act(async () => {
    useMapStore.getState().setReportFilter("tornado");
    vi.advanceTimersByTime(FRAME_INTERVAL_MS);
    for (let turn = 0; turn < 20; turn++) await Promise.resolve();
    expect(useLiveTrackingStore.getState().selectedTime).toBe(first);
  });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(first);
  view.unmount(); queryClient.clear();
});

it("honors a loop change as the last frame settles", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useLiveTrackingStore.getState().selectTime(now - 5 * LIVE_MINUTE);
  useMapStore.getState().setLayerVisible("radar", false);
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  await act(async () => {
    useUiStore.getState().setLoop(true);
    vi.advanceTimersByTime(FRAME_INTERVAL_MS);
    for (let turn = 0; turn < 20; turn++) await Promise.resolve();
    expect(useLiveTrackingStore.getState().playing).toBe(true);
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
  expect(useLiveTrackingStore.getState()).toMatchObject({ playing: true, selectedTime: now });
  view.unmount(); queryClient.clear();
});

it.each([3, 4])("advances loaded live frames at %s times speed", async (speed) => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useUiStore.getState().setSpeed(speed);
  const transport = createTransport(), original = transport.call.bind(transport);
  vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame"
    ? Promise.resolve(frame(Date.parse(String(args?.validTime)))) : original(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const interval = Math.floor(FRAME_INTERVAL_MS / speed);
  await act(async () => { await vi.advanceTimersByTimeAsync(interval - 1); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 60 * LIVE_MINUTE);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 55 * LIVE_MINUTE);
  view.unmount(); queryClient.clear();
});

it("shows the final history frame for a full interval before looping", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useLiveTrackingStore.getState().selectTime(now);
  useUiStore.getState().setLoop(true);
  const transport = createTransport(), original = transport.call.bind(transport);
  vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame"
    ? Promise.resolve(frame(Date.parse(String(args?.validTime)))) : original(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS - 1); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 60 * LIVE_MINUTE);
  view.unmount(); queryClient.clear();
});

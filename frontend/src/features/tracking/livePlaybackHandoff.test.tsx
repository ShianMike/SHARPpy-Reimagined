// @vitest-environment jsdom
import { act, fireEvent, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapKeys } from "../../lib/backend/queryOptions";
import { useRadarFrame } from "../../map/mapQueries";
import { usePngImage } from "../../map/panelScene";
import { cachedPng } from "../../map/pngImages";
import { FRAME_INTERVAL_MS } from "../../map/usePlayback";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { LIVE_MINUTE, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import { LiveTimeline } from "./LiveTimeline";
import { frame, now, setupLivePlaybackTests } from "./livePlaybackTestSetup";
import { useWeatherTime } from "./useWeatherTime";

setupLivePlaybackTests();

it("waits for a refreshed latest radar scan before returning to live", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useLiveTrackingStore.getState().selectTime(now - 5 * LIVE_MINUTE);
  let finish: (value: ArrayBuffer) => void = () => undefined;
  const transport = createTransport(), original = transport.call.bind(transport);
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame"
    ? new Promise((resolve) => { finish = resolve; }) : original(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  queryClient.setQueryData(mapKeys.radar(null), { png: new Blob(["earlier scan"]) }, { updatedAt: now - 1_000 });
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: now - 5 * LIVE_MINUTE, playing: true });
  expect(call.mock.calls.filter(([command]) => command === "get_radar_frame")).toHaveLength(1);
  await act(async () => { finish(frame(now)); await vi.advanceTimersByTimeAsync(0); });
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: null, playing: false });
  view.unmount(); queryClient.clear();
});

it("retains decoded images for all visible live panels", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  const graphics = useGraphicsStore.getState();
  useGraphicsStore.setState({ effective: { ...graphics.effective, profile: "low" } });
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 4096, height: 4096 }));
  useLiveTrackingStore.getState().selectTime(now - 60 * LIVE_MINUTE);
  const mapState = useMapStore.getState();
  mapState.setPanelLayout(4);
  for (let panel = 0; panel < 4; panel++) {
    mapState.setActivePanel(panel);
    mapState.setRadarProduct(`test-${String(panel)}`);
  }
  let finishAlerts: () => void = () => undefined;
  mapState.setLayerVisible("warnings", true);
  const transport = createTransport(), original = transport.call.bind(transport);
  vi.spyOn(transport, "call").mockImplementation((command, args) => {
    if (command === "get_radar_frame") return Promise.resolve(frame(typeof args?.validTime === "string" ? Date.parse(args.validTime) : now));
    if (command === "get_weather_alerts") {
      const reply = original(command, args);
      return new Promise((resolve) => { finishAlerts = () => { void reply.then(resolve); }; });
    }
    return original(command, args);
  });
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
  const time = liveTimestamp(now - 55 * LIVE_MINUTE);
  const frames = Array.from({ length: 4 }, (_, panel) => queryClient.getQueryData<{ png: Blob }>(mapKeys.radar(`test-${String(panel)}`, time)));
  const ready = frames.map((item) => item !== undefined && cachedPng(item.png) !== undefined);
  await act(async () => { finishAlerts(); await vi.advanceTimersByTimeAsync(0); });
  view.unmount(); queryClient.clear(); useGraphicsStore.setState(graphics, true);
  expect(frames.every((item) => item !== undefined)).toBe(true);
  expect(ready).toEqual([true, true, true, true]);
});

it("keeps the prepared live radar visible after the panel subscribes", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useLiveTrackingStore.getState().selectTime(now - 5 * LIVE_MINUTE);
  let decodes = 0;
  vi.stubGlobal("createImageBitmap", vi.fn().mockImplementation(() => ++decodes === 3
    ? new Promise(() => undefined) : Promise.resolve({ width: 2, height: 2 })));
  const transport = createTransport(), original = transport.call.bind(transport);
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame"
    ? Promise.resolve(frame(typeof args?.validTime === "string" ? Date.parse(args.validTime) : now)) : original(command, args));
  function RadarProbe() {
    const radarFrame = useRadarFrame(null, true, useWeatherTime());
    const scene = usePngImage(radarFrame, true, "default", false);
    return <output data-testid="radar-scene">{scene.image === null ? "empty" : "ready"}</output>;
  }
  const { view, queryClient } = renderWithBackend(<><RadarProbe /><LiveTimeline /></>, transport);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByTestId("radar-scene").textContent).toBe("ready");
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
  expect(useLiveTrackingStore.getState().selectedTime).toBeNull();
  expect.soft(screen.getByTestId("radar-scene").textContent).toBe("ready");
  expect.soft(call.mock.calls.filter(([command, args]) => command === "get_radar_frame" && args?.validTime === undefined)).toHaveLength(1);
  view.unmount(); queryClient.clear();
});

it("holds the displayed history frame while the minute clock moves past its time", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  const first = now - 60 * LIVE_MINUTE, next = now - 55 * LIVE_MINUTE;
  useLiveTrackingStore.getState().selectTime(first);
  const transport = createTransport(), original = transport.call.bind(transport);
  let finish: (value: ArrayBuffer) => void = () => undefined;
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_radar_frame"
    ? new Promise((resolve) => { finish = resolve; }) : original(command, args));
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(LIVE_MINUTE); });
  expect(useLiveTrackingStore.getState()).toMatchObject({ now: now + LIVE_MINUTE, selectedTime: first });
  expect(call.mock.calls.filter(([command]) => command === "get_radar_frame").map(([, args]) => args?.validTime)).toEqual([liveTimestamp(next)]);
  await act(async () => { finish(frame(next)); await vi.advanceTimersByTimeAsync(FRAME_INTERVAL_MS); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(next);
  view.unmount(); queryClient.clear();
});

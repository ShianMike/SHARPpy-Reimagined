// @vitest-environment jsdom
import radar from "@contracts/public/map/radar.shrf?url&inline";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { encodeShrf, parseRadarFrame, parseShrf } from "../../lib/contracts/frames";
import { mapKeys } from "../../lib/backend/queryOptions";
import { useAppStore } from "../../stores/appStore";
import { LIVE_MINUTE, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import { LiveTimeline } from "./LiveTimeline";
import { LiveRadarControls } from "./LiveRadarControls";

const map = useMapStore.getState(), clock = useLiveTrackingStore.getState(), app = useAppStore.getState();
const now = Date.parse("2026-10-17T08:00:00Z");
const source = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
const image = (time: number) => encodeShrf({ ...source.header, validTime: liveTimestamp(time) }, source.body);
function Playback() {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  return <><LiveRadarControls scansRef={setTarget} /><LiveTimeline scanTarget={target} /></>;
}
beforeEach(() => {
  useMapStore.setState(map, true); useLiveTrackingStore.setState({ ...clock, now, selectedTime: null, playing: false }, true);
  useAppStore.setState(app, true); useAppStore.getState().setActiveWorkspace("live");
  vi.spyOn(Date, "now").mockReturnValue(now);
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 2, height: 2 }));
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("keeps unknown, loading, provider gaps, load failures and delayed scans distinct, and retries one frame", async () => {
  const transport = createTransport(), original = transport.call.bind(transport);
  let release: (frame: ArrayBuffer) => void = () => undefined;
  let fail = true;
  const slow = new Promise<ArrayBuffer>((resolve) => { release = resolve; });
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => {
    if (command !== "get_radar_frame") return original(command, args);
    const time = Date.parse(String(args?.validTime));
    if (time === now) return slow;
    if (time === now - 5 * LIVE_MINUTE) return Promise.reject(Object.assign(new Error("No published scan."), { code: "OVERLAY_FETCH_FAILED", retryable: true, details: { reason: "scanMissing" } }));
    if (time === now - 10 * LIVE_MINUTE && fail) return Promise.reject(new Error("Network timeout"));
    return Promise.resolve(image(time === now - 15 * LIVE_MINUTE ? time - 12 * LIVE_MINUTE : time));
  });
  const { view, queryClient } = renderWithBackend(<Playback />, transport);
  const trigger = screen.getByRole("button", { name: "Choose radar scan" });
  expect(trigger.compareDocumentPosition(screen.getByRole("button", { name: "Site Tilt" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(screen.getByTestId("live-timeline")).queryByRole("button", { name: "Cache past hour" })).toBeNull();
  expect(within(screen.getByTestId("live-timeline")).queryByRole("group", { name: "Radar scan availability" })).toBeNull();
  fireEvent.click(trigger);
  expect(screen.getByRole("dialog", { name: "Radar scans" })).toBeTruthy();
  const scans = within(screen.getByRole("group", { name: "Radar scan availability" }));
  expect(scans.getAllByRole("button", { name: /Not loaded/u })).toHaveLength(13);
  expect(call.mock.calls.filter(([command]) => command === "get_radar_frame")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Cache past hour" }));
  await waitFor(() => { expect(scans.getByRole("button", { name: "08:00 UTC · Loading" })).toBeTruthy(); });
  await waitFor(() => { expect(scans.getAllByRole("button", { name: /Available/u })).toHaveLength(9); });
  expect(scans.getByRole("button", { name: "07:55 UTC · Missing" }).getAttribute("aria-description")).toContain("No published scan");
  expect(scans.getByRole("button", { name: "07:45 UTC · Delayed" }).getAttribute("aria-description")).toContain("12 minutes behind · Scan 2026-10-17 07:33Z UTC");
  expect(scans.getByRole("button", { name: "07:50 UTC · Load failed" })).toBeTruthy();
  await act(async () => { release(image(now)); await slow; });
  await waitFor(() => { expect(scans.getByRole("button", { name: "08:00 UTC · Available" })).toBeTruthy(); });
  call.mockClear(); fail = false;
  fireEvent.click(scans.getByRole("button", { name: "07:50 UTC · Load failed" }));
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: now - 10 * LIVE_MINUTE, playing: false });
  await waitFor(() => { expect(scans.getByRole("button", { name: "07:50 UTC · Available" }).getAttribute("aria-pressed")).toBe("true"); });
  expect(call.mock.calls.filter(([command]) => command === "get_radar_frame")).toHaveLength(1);
  act(() => { useMapStore.getState().setRadarProduct("echo-tops"); });
  expect(scans.getAllByRole("button", { name: /Not loaded/u })).toHaveLength(13);
  act(() => { useMapStore.getState().setLayerVisible("radar", false); });
  expect(screen.queryByRole("group", { name: "Radar scan availability" })).toBeNull();
  view.unmount(); queryClient.clear();
});

it("combines visible radar sources, updates background cache results, and isolates elevations", () => {
  useMapStore.getState().setPanelLayout(2);
  useMapStore.getState().setActivePanel(1);
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite("KTLX");
  useMapStore.getState().setRadarProduct("velocity");
  useMapStore.getState().setRadarTilt("1");
  useLiveTrackingStore.setState({ selectedTime: now });
  const { view, queryClient } = renderWithBackend(<Playback />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Choose radar scan" }));
  const time = liveTimestamp(now);
  const mosaic = parseRadarFrame(image(now - 10 * LIVE_MINUTE));
  act(() => {
    queryClient.setQueryData(mapKeys.radar(null, time), mosaic);
    queryClient.setQueryData(mapKeys.radarSite("velocity", "KTLX", time, "1"), { ...mosaic, label: "Velocity", validTime: liveTimestamp(now - 11 * LIVE_MINUTE) });
  });
  const delayed = screen.getByRole("button", { name: "08:00 UTC · Delayed" });
  expect(delayed.getAttribute("aria-description")).toContain("Mosaic · Composite Reflectivity: Available");
  expect(delayed.getAttribute("aria-description")).toContain("KTLX · Velocity: 11 minutes behind");
  act(() => { useMapStore.getState().setRadarTilt("2"); });
  expect(screen.getByRole("button", { name: "08:00 UTC · Not loaded" })).toBeTruthy();
  act(() => { useMapStore.getState().setRadarTilt("1"); });
  expect(screen.getByRole("button", { name: "08:00 UTC · Delayed" })).toBeTruthy();
  act(() => { useMapStore.getState().setLayerVisible("radar", false); });
  expect(screen.getByRole("button", { name: "08:00 UTC · Available" })).toBeTruthy();
  view.unmount(); queryClient.clear();
});

it("selects a cached time, closes the picker, and returns keyboard focus to its toolbar button", async () => {
  useLiveTrackingStore.setState({ selectedTime: now });
  const { view, queryClient } = renderWithBackend(<Playback />, createTransport());
  const selected = now - 30 * LIVE_MINUTE;
  act(() => { queryClient.setQueryData(mapKeys.radar(null, liveTimestamp(selected)), parseRadarFrame(image(selected))); });
  const trigger = screen.getByRole("button", { name: "Choose radar scan" });
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "07:30 UTC · Available" }));
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Radar scans" })).toBeNull(); });
  expect(document.activeElement).toBe(trigger);
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: selected, playing: false });
  fireEvent.click(trigger);
  fireEvent.keyDown(screen.getByRole("dialog", { name: "Radar scans" }), { key: "Escape" });
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Radar scans" })).toBeNull(); });
  expect(document.activeElement).toBe(trigger);
  view.unmount(); queryClient.clear();
});

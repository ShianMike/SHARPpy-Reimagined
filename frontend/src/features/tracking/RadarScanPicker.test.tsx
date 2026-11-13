// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapKeys } from "../../lib/backend/queryOptions";
import { useAppStore } from "../../stores/appStore";
import { LIVE_MINUTE, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import { LiveTimeline } from "./LiveTimeline";
import { LiveRadarControls } from "./LiveRadarControls";

const map = useMapStore.getState(), clock = useLiveTrackingStore.getState(), app = useAppStore.getState();
const now = Date.parse("2026-10-17T08:00:00Z");
function Playback() {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  return <><LiveRadarControls scansRef={setTarget} /><LiveTimeline scanTarget={target} /></>;
}
beforeEach(() => {
  useMapStore.setState(map, true); useLiveTrackingStore.setState({ ...clock, now, selectedTime: null, playing: false }, true);
  useAppStore.setState(app, true); useAppStore.getState().setActiveWorkspace("live");
  vi.spyOn(Date, "now").mockReturnValue(now);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("renders Radar time in the toolbar with Live default, current viewing time, and no scan grid or downloads", () => {
  const transport = createTransport();
  const call = vi.spyOn(transport, "call");
  const { view, queryClient } = renderWithBackend(<Playback />, transport);
  const trigger = screen.getByRole("button", { name: "Choose radar time" });
  expect(trigger.textContent).toContain("Radar time");
  expect(trigger.textContent).toContain("Live");
  expect(trigger.compareDocumentPosition(screen.getByRole("button", { name: "Site Tilt" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(screen.getByTestId("live-timeline")).queryByRole("button", { name: "Choose radar time" })).toBeNull();
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Radar time" });
  expect(within(dialog).getByText("2026-10-17 08:00 UTC")).toBeTruthy();
  expect(within(dialog).getByText("Following live")).toBeTruthy();
  expect(within(dialog).getByRole("button", { name: "Return to Live" }).hasAttribute("disabled")).toBe(true);
  expect(screen.queryByRole("dialog", { name: "Radar scans" })).toBeNull();
  expect(screen.queryByRole("group", { name: "Radar scan availability" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Cache past hour" })).toBeNull();
  expect(call.mock.calls.filter(([command]) => command === "get_radar_frame" || command === "get_radar_site_frame" || command === "get_radar_tilts")).toHaveLength(0);
  view.unmount(); queryClient.clear();
});

it("selects any UTC minute in the past hour directly, pauses playback, and handles UTC midnight", async () => {
  const transport = createTransport();
  const call = vi.spyOn(transport, "call");
  const { view, queryClient } = renderWithBackend(<Playback />, transport);
  call.mockClear();
  const trigger = screen.getByRole("button", { name: "Choose radar time" });
  fireEvent.click(trigger);
  const input = screen.getByRole("textbox", { name: "UTC time" });
  fireEvent.change(input, { target: { value: "07:42" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply UTC time" }));
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Radar time" })).toBeNull(); });
  expect(document.activeElement).toBe(trigger);
  expect(trigger.textContent).toContain("07:42 UTC");
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: now - 18 * LIVE_MINUTE, playing: false });
  expect(screen.getByLabelText("Live viewing time").textContent).toContain("10-17 07:42Z UTC");
  expect(call.mock.calls.filter(([command]) => command === "get_radar_frame" || command === "get_radar_site_frame" || command === "get_radar_tilts")).toHaveLength(0);

  useLiveTrackingStore.setState({ playing: true });
  fireEvent.click(trigger);
  fireEvent.change(screen.getByRole("textbox", { name: "UTC time" }), { target: { value: "07:35" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply UTC time" }));
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Radar time" })).toBeNull(); });
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: now - 25 * LIVE_MINUTE, playing: false });
  view.unmount(); queryClient.clear();

  const midnightNow = Date.parse("2026-10-17T00:25:00Z");
  vi.spyOn(Date, "now").mockReturnValue(midnightNow);
  useLiveTrackingStore.setState({ now: midnightNow, selectedTime: null, playing: false });
  const midnight = renderWithBackend(<Playback />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Choose radar time" }));
  fireEvent.change(screen.getByRole("textbox", { name: "UTC time" }), { target: { value: "23:40" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply UTC time" }));
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Radar time" })).toBeNull(); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(Date.parse("2026-10-16T23:40:00Z"));
  midnight.view.unmount(); midnight.queryClient.clear();
});

it("steps five minutes, allows precise slider selection, and rejects out-of-window UTC times", () => {
  const { view, queryClient } = renderWithBackend(<Playback />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Choose radar time" }));
  const dialog = screen.getByRole("dialog", { name: "Radar time" });
  fireEvent.change(within(dialog).getByRole("textbox", { name: "UTC time" }), { target: { value: "06:59" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply UTC time" }));
  expect(within(dialog).getByRole("alert").textContent).toContain("07:00 and 08:00 UTC");
  expect(useLiveTrackingStore.getState().selectedTime).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: "Previous 5 minutes" }));
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 5 * LIVE_MINUTE);
  expect(within(dialog).getByText("2026-10-17 07:55 UTC")).toBeTruthy();
  fireEvent.change(within(dialog).getByRole("slider", { name: "Past hour minute" }), { target: { value: "-23" } });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 23 * LIVE_MINUTE);
  expect(within(dialog).getByText("2026-10-17 07:37 UTC")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Next 5 minutes" }));
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 18 * LIVE_MINUTE);
  expect(within(dialog).getByText("2026-10-17 07:42 UTC")).toBeTruthy();
  fireEvent.change(within(dialog).getByRole("slider", { name: "Past hour minute" }), { target: { value: "-58" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Previous 5 minutes" }));
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 60 * LIVE_MINUTE);
  expect(within(dialog).getByRole("button", { name: "Previous 5 minutes" }).hasAttribute("disabled")).toBe(true);
  fireEvent.change(within(dialog).getByRole("slider", { name: "Past hour minute" }), { target: { value: "-2" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Next 5 minutes" }));
  expect(useLiveTrackingStore.getState().selectedTime).toBeNull();
  view.unmount(); queryClient.clear();
});

it("keeps a partially entered time when the live minute changes", () => {
  const { view, queryClient } = renderWithBackend(<Playback />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Choose radar time" }));
  const input = screen.getByRole<HTMLInputElement>("textbox", { name: "UTC time" });
  fireEvent.change(input, { target: { value: "07:4" } });
  act(() => { useLiveTrackingStore.getState().tick(now + LIVE_MINUTE); });
  expect(input.value).toBe("07:4");
  view.unmount(); queryClient.clear();
});

it("distinguishes the requested time from the actual scan, including a scan reused by the provider", async () => {
  const { view, queryClient } = renderWithBackend(<Playback />, createTransport());
  const scan = "2026-10-17T07:54:00Z";
  act(() => { queryClient.setQueryData(mapKeys.radar(null, liveTimestamp(now)), { validTime: scan }); });
  fireEvent.click(screen.getByRole("button", { name: "Choose radar time" }));
  const dialog = screen.getByRole("dialog", { name: "Radar time" });
  expect(within(dialog).getByText("Requested · UTC")).toBeTruthy();
  await waitFor(() => { expect(within(dialog).getByText("Radar scan · 2026-10-17 07:54:00 UTC")).toBeTruthy(); });
  fireEvent.click(within(dialog).getByRole("button", { name: "Previous 5 minutes" }));
  expect(within(dialog).getByText("No radar scan loaded")).toBeTruthy();
  act(() => { queryClient.setQueryData(mapKeys.radar(null, liveTimestamp(now - 5 * LIVE_MINUTE)), { validTime: scan }); });
  await waitFor(() => { expect(within(dialog).getByText("Radar scan · 2026-10-17 07:54:00 UTC")).toBeTruthy(); });
  view.unmount(); queryClient.clear();
});

it("returns to Live, closes the picker, and restores keyboard focus to its toolbar button", async () => {
  useLiveTrackingStore.setState({ selectedTime: now - 30 * LIVE_MINUTE });
  const { view, queryClient } = renderWithBackend(<Playback />, createTransport());
  const trigger = screen.getByRole("button", { name: "Choose radar time" });
  expect(trigger.textContent).toContain("07:30 UTC");
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "Return to Live" }));
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Radar time" })).toBeNull(); });
  expect(document.activeElement).toBe(trigger);
  expect(trigger.textContent).toContain("Live");
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: null, playing: false });
  fireEvent.click(trigger);
  fireEvent.keyDown(screen.getByRole("dialog", { name: "Radar time" }), { key: "Escape" });
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Radar time" })).toBeNull(); });
  expect(document.activeElement).toBe(trigger);
  view.unmount(); queryClient.clear();
});

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
  vi.spyOn(Date, "now").mockReturnValue(now);
  useMapStore.setState(map, true); useLiveTrackingStore.setState({ ...clock, now, selectedTime: null, playing: false }, true);
  useAppStore.setState(app, true); useAppStore.getState().setActiveWorkspace("live");
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("returns to live when another radar is chosen in the site picker", async () => {
  const { view, queryClient } = renderWithBackend(<LiveRadarControls />, createTransport());
  fireEvent.click(screen.getByRole("combobox", { name: "Site" }));
  fireEvent.click(await screen.findByRole("option", { name: "KTLX" }));
  useLiveTrackingStore.setState({ selectedTime: now - 5 * LIVE_MINUTE, playing: true });
  fireEvent.click(screen.getByRole("combobox", { name: "Site" }));
  fireEvent.click(await screen.findByRole("option", { name: "KABR" }));
  expect(useMapStore.getState().livePanels[0]?.radarSite).toBe("KABR");
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: null, playing: false });
  view.unmount(); queryClient.clear();
});

it("shows the actual latest scan automatically, including its age and delay", async () => {
  const transport = createTransport();
  const call = vi.spyOn(transport, "call");
  const { view, queryClient } = renderWithBackend(<Playback />, transport);
  act(() => { queryClient.setQueryData(mapKeys.radar(null), { validTime: "2026-10-17T07:41:00Z" }); });
  const trigger = screen.getByRole("button", { name: "Radar scan time" });
  await waitFor(() => { expect(trigger.textContent).toContain("07:41 UTC"); });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Radar time" });
  expect(within(dialog).getByText("Latest scan")).toBeTruthy();
  expect(within(dialog).getByText("2026-10-17 07:41:00 UTC")).toBeTruthy();
  expect(within(dialog).getByText("19 min old · Delayed scan")).toBeTruthy();
  expect(within(dialog).queryByRole("textbox")).toBeNull();
  expect(within(dialog).queryByRole("slider")).toBeNull();
  expect(call.mock.calls.filter(([command]) => command === "get_radar_frame" || command === "get_radar_site_frame")).toHaveLength(0);
  act(() => { queryClient.setQueryData(mapKeys.radar(null), { validTime: "2026-10-17T07:57:00Z" }); });
  await waitFor(() => { expect(within(dialog).getByText("2026-10-17 07:57:00 UTC")).toBeTruthy(); });
  expect(within(dialog).getByText("3 min old")).toBeTruthy();
  view.unmount(); queryClient.clear();
});

it("identifies an unavailable scan without inventing a timestamp", () => {
  const { view, queryClient } = renderWithBackend(<Playback />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Radar scan time" }));
  const dialog = screen.getByRole("dialog", { name: "Radar time" });
  expect(within(dialog).getByText("Scan time unavailable")).toBeTruthy();
  expect(within(dialog).getByRole("status").textContent).toContain("No radar scan loaded");
  view.unmount(); queryClient.clear();
});

it("shows the actual historical scan and returns to automatic live updates", async () => {
  useLiveTrackingStore.setState({ selectedTime: now - 30 * LIVE_MINUTE });
  const { view, queryClient } = renderWithBackend(<Playback />, createTransport());
  act(() => { queryClient.setQueryData(mapKeys.radar(null, liveTimestamp(now - 30 * LIVE_MINUTE)), { validTime: "2026-10-17T07:26:00Z" }); });
  const trigger = screen.getByRole("button", { name: "Radar scan time" });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Radar time" });
  await waitFor(() => { expect(within(dialog).getByText("2026-10-17 07:26:00 UTC")).toBeTruthy(); });
  expect(within(dialog).getByText("Viewing 2026-10-17 07:30:00 UTC")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Return to Live" }));
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Radar time" })).toBeNull(); });
  expect(document.activeElement).toBe(trigger);
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: null, playing: false });
  view.unmount(); queryClient.clear();
});

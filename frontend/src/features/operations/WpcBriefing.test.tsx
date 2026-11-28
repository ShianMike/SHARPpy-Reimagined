// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { wpcOutlookFor } from "../../lib/backend/fixtureFrames";
import { createTransport } from "../../lib/backend/fixtureTransport";
import type { Transport } from "../../lib/backend/transport";
import { useForecastStore } from "../../stores/forecastStore";
import { useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { renderWithBackend } from "../../test/backendHarness";
import { WpcBriefing } from "./WpcBriefing";

const savedForecast = useForecastStore.getState();
const savedLive = useLiveTrackingStore.getState();
const now = new Date("2026-10-04T23:58:00Z");
const current = wpcOutlookFor({ validTime: "2026-10-04T23:58:00Z", day: 1 });

beforeEach(() => {
  vi.spyOn(Date, "now").mockReturnValue(now.getTime());
});

afterEach(() => {
  cleanup();
  useForecastStore.setState(savedForecast);
  useLiveTrackingStore.setState(savedLive);
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function transportWith(reply: unknown = current) {
  const base = createTransport();
  const call = vi.fn<Transport["call"]>((command, args) =>
    command === "get_wpc_outlook" ? Promise.resolve(reply) : base.call(command, args));
  return { ...base, call };
}

it("shows Day 1 ERO risk, affected states, times, and expandable discussion summary", async () => {
  const { queryClient } = renderWithBackend(<WpcBriefing />, transportWith());
  await screen.findByRole("heading", { name: "Slight (At Least 15%)" });
  expect(screen.getByText("Weather Prediction Center")).toBeTruthy();
  expect(screen.getByText("Current", { exact: true })).toBeTruthy();
  expect(screen.getByText("Oklahoma, Texas")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Forecast discussion" })).toBeTruthy();
  expect(screen.getByText(/there is a slight risk of excessive rainfall/i)).toBeTruthy();
  const toggle = screen.getByRole("button", { name: "Read full discussion", expanded: false });
  const body = document.getElementById(toggle.getAttribute("aria-controls") ?? "");
  expect(body).toMatchObject({ hidden: true });
  await userEvent.click(toggle);
  expect(screen.getByRole("heading", { name: "Southern Plains" })).toBeTruthy();
  expect(screen.getByText(/Persistent low-level moisture transport/)).toBeTruthy();
  await userEvent.click(toggle);
  expect(screen.queryByRole("heading", { name: "Southern Plains" })).toBeNull();
  expect(body).toMatchObject({ hidden: true, isConnected: true });
  expect(body?.hasAttribute("inert")).toBe(true);
  queryClient.clear();
});

it("handles valid quiet <5% days without inventing a risk category", async () => {
  const quiet = structuredClone(current) as Record<string, unknown>;
  const outlook = quiet.outlook as Record<string, unknown>;
  outlook.areas = [];
  outlook.affectedStates = [];
  outlook.discussion = {
    headline: "EXCESSIVE RAINFALL RISK LESS THAN 5% ACROSS CONUS",
    summary: "Brief showers stay light across the Plains today.",
    sections: [{ heading: "Overview", body: "High pressure limits heavy rain rates." }],
    issued: "2026-10-04T12:50:00Z",
    sourceUrl: "https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=curr&day=1",
  };
  const { queryClient } = renderWithBackend(<WpcBriefing />, transportWith(quiet));
  await screen.findByRole("heading", { name: "No areas outlined" });
  expect(screen.getByText("None", { exact: true })).toBeTruthy();
  expect(screen.getByText("Brief showers stay light across the Plains today.")).toBeTruthy();
  queryClient.clear();
});

it("switches between Day 1, Day 2, and Day 3 and updates the WPC link", async () => {
  const transport = transportWith();
  const base = createTransport();
  transport.call.mockImplementation((command, args) =>
    command === "get_wpc_outlook"
      ? Promise.resolve(wpcOutlookFor(args))
      : base.call(command, args));
  const { queryClient } = renderWithBackend(<WpcBriefing />, transport);
  await screen.findByRole("heading", { name: "Slight (At Least 15%)" });
  await userEvent.click(screen.getByRole("button", { name: "Day 3" }));
  await screen.findByRole("button", { name: "Day 3", pressed: true });
  expect(transport.call).toHaveBeenCalledWith("get_wpc_outlook", expect.objectContaining({ day: 3, validTime: "2026-10-04T23:58:00Z" }));
  expect(screen.getByRole("link", { name: "NOAA / NWS WPC" }).getAttribute("href")).toContain("ero.php?opt=curr&day=3");
  queryClient.clear();
});

it.each([
  { ...(current as Record<string, unknown>), outlook: null },
  { ...(current as Record<string, unknown>), outlook: { ...((current as Record<string, unknown>).outlook as Record<string, unknown>), validTo: "2026-10-04T12:00:00Z" } },
  { ...(current as Record<string, unknown>), outlook: { ...((current as Record<string, unknown>).outlook as Record<string, unknown>), validTo: null } },
])("withholds missing, expired and invalid WPC issuances", async (reply) => {
  const { queryClient } = renderWithBackend(<WpcBriefing />, transportWith(reply));
  await screen.findByRole("heading", { name: "Outlook unavailable" });
  expect(screen.queryByText("Current", { exact: true })).toBeNull();
  queryClient.clear();
});

it("identifies failed refreshes while retaining the last valid WPC outlook", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const transport = transportWith();
  const base = createTransport();
  let fail = false;
  transport.call.mockImplementation((command, args) =>
    command === "get_wpc_outlook" && fail
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- backend errors cross IPC as objects
      ? Promise.reject({ code: "OVERLAY_FETCH_FAILED", message: "WPC unavailable", retryable: false })
      : command === "get_wpc_outlook"
        ? Promise.resolve(current)
        : base.call(command, args));
  const { queryClient } = renderWithBackend(<WpcBriefing />, transport);
  await screen.findByRole("heading", { name: "Slight (At Least 15%)" });
  fail = true;
  await userEvent.click(screen.getByRole("button", { name: "Refresh Day 1 WPC outlook" }));
  await screen.findByText("Refresh failed. Showing the last valid outlook.");
  expect(screen.getByRole("heading", { name: "Slight (At Least 15%)" })).toBeTruthy();
  queryClient.clear();
});

// @vitest-environment jsdom
import sample from "@contracts/public/map/outlook.cat.json";
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import type { Transport } from "../../lib/backend/transport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { renderWithBackend } from "../../test/backendHarness";
import { SpcBriefing } from "./SpcBriefing";

const savedApp = useAppStore.getState(), savedForecast = useForecastStore.getState(), savedLive = useLiveTrackingStore.getState();
const now = new Date("2026-10-04T23:58:00Z");
const area = { ...sample.outlook.areas[0], label: "SLGT", description: "Slight Risk", rank: 2, rings: [[[-104, 30], [-88, 30], [-88, 42], [-104, 42]]] };
const current = { ...sample, outlook: { ...sample.outlook, areas: [area, { ...area, hatch: true, rank: 10000, label: "SIGN", description: "Significant severe" }] } };

beforeEach(() => {
  vi.spyOn(Date, "now").mockReturnValue(now.getTime());
  useAppStore.setState({ selectedPoint: null, selectedStationId: null });
});
afterEach(() => {
  cleanup(); useAppStore.setState(savedApp); useForecastStore.setState(savedForecast); useLiveTrackingStore.setState(savedLive);
  vi.useRealTimers(); vi.restoreAllMocks();
});

function transportWith(reply: unknown = current) {
  const base = createTransport();
  const call = vi.fn<Transport["call"]>((command, args) => command === "get_spc_outlook" ? Promise.resolve(reply) : base.call(command, args));
  return { ...base, call };
}

it("shows issue and valid times without an embedded map and ignores hatch rank in the headline", async () => {
  const { queryClient } = renderWithBackend(<SpcBriefing />, transportWith());
  await screen.findByRole("heading", { name: "Slight Risk" });
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText("04 Oct, 12:50 UTC")).toBeTruthy();
  expect(screen.getByText("05 Oct, 12:00 UTC")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Significant severe" })).toBeNull();
  queryClient.clear();
});

it("requests only the selected product using the current clock, independent of archived forecast and live history", async () => {
  useForecastStore.setState({ runTime: "2024-02-29T12:00:00Z", forecastHour: 6 });
  useLiveTrackingStore.setState({ selectedTime: Date.parse("2024-02-29T18:00:00Z") });
  const transport = transportWith();
  const { queryClient } = renderWithBackend(<SpcBriefing />, transport);
  await screen.findByRole("heading", { name: "Slight Risk" });
  const requests = () => transport.call.mock.calls.filter(([command]) => command === "get_spc_outlook");
  expect(requests()).toHaveLength(1);
  expect(requests()[0]?.[1]?.product).toBe("cat");
  expect(requests()[0]?.[1]?.validTime).toBe("2026-10-04T23:58:00Z");
  await userEvent.click(screen.getByRole("button", { name: "Tornado" }));
  await waitFor(() => { expect(requests()).toHaveLength(2); });
  expect(requests()[1]?.[1]?.product).toBe("torn");
  expect(screen.getByRole("button", { name: "Tornado" }).getAttribute("aria-pressed")).toBe("true");
  expect(useForecastStore.getState()).toMatchObject({ runTime: "2024-02-29T12:00:00Z", forecastHour: 6 });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(Date.parse("2024-02-29T18:00:00Z"));
  queryClient.clear();
});

it("reports selected point risk and significant-severe qualifiers, respecting polygon holes", async () => {
  useAppStore.setState({ selectedPoint: { longitude: -95, latitude: 35 } });
  const { queryClient } = renderWithBackend(<SpcBriefing />, transportWith());
  await screen.findByRole("heading", { name: "Slight Risk" });
  expect(screen.getByText("Significant-severe area also outlined")).toBeTruthy();
  act(() => { useAppStore.setState({ selectedPoint: { longitude: -75, latitude: 30 } }); });
  expect(screen.getByText("No outlined area at this point")).toBeTruthy();
  act(() => { queryClient.setQueryData(["spc-current", "cat"], { ...current, outlook: { ...current.outlook, areas: [{ ...area, rings: [...area.rings.slice(0, 1), [[-98, 32], [-90, 32], [-90, 40], [-98, 40]]] }] } }); useAppStore.setState({ selectedPoint: { longitude: -95, latitude: 35 } }); });
  expect(screen.getByText("No outlined area at this point")).toBeTruthy();
  queryClient.clear();
});

it.each([
  { ...current, outlook: null },
  { ...current, outlook: { ...current.outlook, covers: false } },
  { ...current, outlook: { ...current.outlook, validTo: "2026-10-04T12:00:00Z" } },
  { ...current, outlook: { ...current.outlook, validFrom: "2026-10-05T01:00:00Z" } },
  { ...current, outlook: { ...current.outlook, issued: "2026-10-05T01:00:00Z" } },
  { ...current, outlook: { ...current.outlook, validTo: null } },
])("never presents missing, expired, future or uncovered outlooks as current", async (reply) => {
  const { queryClient } = renderWithBackend(<SpcBriefing />, transportWith(reply));
  await screen.findByRole("heading", { name: "No current outlook available" });
  expect(screen.queryByText("Current", { exact: true })).toBeNull();
  expect(screen.queryByRole("img")).toBeNull();
  queryClient.clear();
});

it("handles empty valid guidance without inventing a risk category", async () => {
  const { queryClient } = renderWithBackend(<SpcBriefing />, transportWith({ ...current, outlook: { ...current.outlook, areas: [] } }));
  await screen.findByRole("heading", { name: "No areas outlined" });
  expect(screen.getByText("Current", { exact: true })).toBeTruthy();
  queryClient.clear();
});

it("labels a provider fallback by its actual outlook day instead of calling it Day 1", async () => {
  const { queryClient } = renderWithBackend(<SpcBriefing />, transportWith({ ...current, outlook: { ...current.outlook, title: "SPC convective outlook — Day 2 categorical risk" } }));
  await screen.findByText("Storm Prediction Center · Day 2");
  expect(screen.queryByText("Storm Prediction Center · Day 1")).toBeNull();
  queryClient.clear();
});

it("retries unavailable guidance and identifies failed refreshes", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const transport = transportWith();
  const base = createTransport();
  let fail = true;
  transport.call.mockImplementation((command, args) => command === "get_spc_outlook" && fail
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- backend errors cross IPC as objects
    ? Promise.reject({ code: "OVERLAY_FETCH_FAILED", message: "SPC unavailable", retryable: false }) : command === "get_spc_outlook" ? Promise.resolve(current) : base.call(command, args));
  const { queryClient } = renderWithBackend(<SpcBriefing />, transport);
  await screen.findByRole("heading", { name: "Outlook unavailable" });
  fail = false;
  await userEvent.click(screen.getByRole("button", { name: "Refresh current outlook" }));
  await screen.findByRole("heading", { name: "Slight Risk" });
  fail = true;
  await userEvent.click(screen.getByRole("button", { name: "Refresh current outlook" }));
  await screen.findByText("Refresh failed. Showing the last valid outlook.");
  expect(screen.getByRole("heading", { name: "Slight Risk" })).toBeTruthy();
  queryClient.clear();
});

it("rechecks the current UTC clock at midnight and hides guidance once its valid period ends", async () => {
  vi.restoreAllMocks(); vi.useFakeTimers(); vi.setSystemTime(now);
  const transport = transportWith();
  const { queryClient } = renderWithBackend(<SpcBriefing />, transport);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByRole("heading", { name: "Slight Risk" })).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60_000); });
  const requests = transport.call.mock.calls.filter(([command]) => command === "get_spc_outlook");
  expect(requests).toHaveLength(2);
  expect(requests[1]?.[1]?.validTime).toBe("2026-10-05T00:03:00Z");
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
  await act(async () => { window.dispatchEvent(new Event("focus")); await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByRole("heading", { name: "No current outlook available" })).toBeTruthy();
  expect(within(screen.getByRole("group", { name: "SPC outlook product" })).getAllByRole("button")).toHaveLength(4);
  queryClient.clear();
});

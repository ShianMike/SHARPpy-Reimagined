// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendFailure } from "../../lib/backend/errors";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { LIVE_MINUTE, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import type { BackendError } from "../../types/contracts";
import { OverlayErrorNotice } from "./OverlayErrorNotice";
import { WeatherAlertStatus } from "./WeatherAlertStatus";

const app = useAppStore.getInitialState(), map = useMapStore.getInitialState(), clock = useLiveTrackingStore.getInitialState();
const now = Date.parse("2026-10-30T02:47:00Z");
const history: BackendError = { code: "OVERLAY_FETCH_FAILED", retryable: false, message: "No session snapshot",
  context: { operation: "get_weather_alerts", provider: "python-bridge" }, details: { reason: "historyUnavailable" } };

beforeEach(() => {
  useAppStore.setState(app, true); useMapStore.setState(map, true);
  useAppStore.getState().setActiveWorkspace("live");
  useLiveTrackingStore.setState({ ...clock, now, selectedTime: now - 5 * LIVE_MINUTE }, true);
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => { cleanup(); useLiveTrackingStore.setState(clock, true); useMapStore.setState(map, true); useAppStore.setState(app, true); vi.restoreAllMocks(); });

it("explains missing alert history as a warning and returns to current live data", () => {
  useLiveTrackingStore.getState().setPlaying(true);
  const retry = vi.fn();
  const { queryClient } = renderWithBackend(<OverlayErrorNotice error={new BackendFailure(history)} operation="get_weather_alerts" onRetry={retry} testId="gap" />, createTransport());
  const notice = screen.getByTestId("gap");
  expect(notice.getAttribute("data-tone")).toBe("warning");
  expect(notice.textContent).toContain("Alert status at this time is unknown");
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Return to Live" }));
  expect(useLiveTrackingStore.getState()).toMatchObject({ playing: false, selectedTime: null });
  expect(useLiveTrackingStore.getState().now).toBe(Math.floor(Date.now() / LIVE_MINUTE) * LIVE_MINUTE);
  expect(retry).not.toHaveBeenCalled();
  queryClient.clear();
});

it("offers retry for a missing radar scan while retaining actual provider errors", () => {
  const retry = vi.fn();
  const missing: BackendError = { ...history, retryable: true, context: { operation: "get_radar_site_frame" }, details: { reason: "scanMissing" } };
  const { queryClient } = renderWithBackend(<OverlayErrorNotice error={new BackendFailure(missing)} operation="get_radar_site_frame" onRetry={retry} testId="gap" />, createTransport());
  expect(screen.getByTestId("gap").textContent).toContain("Radar scan unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledOnce();
  queryClient.clear();
});

it("keeps a connection or decode failure as an error with its producer message", () => {
  const failed = new BackendFailure({ ...history, retryable: true, details: {}, message: "Radar response could not be decoded" });
  const { queryClient } = renderWithBackend(<OverlayErrorNotice error={failed} operation="get_weather_alerts" onRetry={vi.fn()} testId="failure" />, createTransport());
  expect(screen.getByRole("alert").getAttribute("data-tone")).toBe("danger");
  expect(screen.getByRole("alert").textContent).toContain(failed.message);
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  queryClient.clear();
});

it.each(["historyUnavailable", "providerFailure"] as const)("only suppresses remount retries for missing history: %s", async (reason) => {
  const transport = createTransport(), original = transport.call.bind(transport);
  const error = { ...history, details: { reason } };
  const call = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_weather_alerts"
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- public backend error contract
    ? Promise.reject(error) : original(command, args));
  function Controls() {
    const [shown, setShown] = useState(true);
    return <><button onClick={() => { setShown(!shown); }}>Toggle controls</button>{shown ? <><WeatherAlertStatus kind="warning" /><WeatherAlertStatus kind="watch" /></> : null}</>;
  }
  const { queryClient } = renderWithBackend(<Controls />, transport);
  await waitFor(() => { expect(screen.getAllByTestId("weather-alerts-error")).toHaveLength(2); });
  fireEvent.click(screen.getByRole("button", { name: "Toggle controls" }));
  fireEvent.click(screen.getByRole("button", { name: "Toggle controls" }));
  await waitFor(() => { expect(screen.getAllByTestId("weather-alerts-error")).toHaveLength(2); });
  expect(call.mock.calls.filter(([command]) => command === "get_weather_alerts")).toHaveLength(reason === "historyUnavailable" ? 1 : 2);
  expect(screen.queryByText(/0 active/u)).toBeNull();
  queryClient.clear();
});

// @vitest-environment jsdom
import fieldBytes from "@contracts/public/map/field.shrf?url&inline";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapKeys } from "../../lib/backend/queryOptions";
import type { useFrameDownload } from "../../map/useFrameDownload";
import { useForecastStore } from "../../stores/forecastStore";
import { useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import { ForecastControls } from "../toolbar/ForecastControls";
import { TimelineRegion } from "./TimelineRegion";
import { ForecastDownloads } from "./ForecastDownloads";

const forecastInitial = useForecastStore.getState();
const mapInitial = useMapStore.getState();
afterEach(() => { cleanup(); useForecastStore.setState(forecastInitial); useMapStore.setState(mapInitial); vi.restoreAllMocks(); });

it("embeds download counts, cache recovery and bulk retry without another popup", async () => {
  const user = userEvent.setup();
  const toggle = vi.fn(), refresh = vi.fn();
  const download: ReturnType<typeof useFrameDownload> = { toggle, refresh, retryHour: vi.fn(), running: false, complete: false,
    loaded: 17, total: 19, cached: 17, ready: 2, message: "17 saved; 2 in memory", started: true,
    failed: true, missing: [3, 5], failedHours: [3], cacheError: false, disabled: false, hourStates: [{ hour: 3, state: "failed" }] };
  render(<ForecastDownloads download={download} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Forecast Hours" })).toBeNull();
  expect(screen.getByLabelText("Forecast cache summary").textContent).toContain("17 saved");
  expect(screen.getByLabelText("Forecast cache summary").textContent).toContain("2 ready");
  await user.click(screen.getByRole("button", { name: "Check Cache" }));
  expect(refresh).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Retry Missing Hours" }));
  expect(toggle).toHaveBeenCalledOnce();
  expect(screen.getByText(/Select a red hour/u)).toBeTruthy();
});

it("shows loading, stopping, completion and cache errors in the inline controls", async () => {
  const user = userEvent.setup();
  const download: ReturnType<typeof useFrameDownload> = { toggle: vi.fn(), refresh: vi.fn(), retryHour: vi.fn(), running: false, complete: false,
    loaded: 0, total: 19, cached: 0, ready: 1, message: "0 of 19 hours saved", started: false,
    failed: false, missing: Array.from({ length: 19 }, (_, hour) => hour), failedHours: [], cacheError: false, disabled: false, hourStates: [] };
  const { rerender } = render(<ForecastDownloads download={download} />);
  await user.click(screen.getByRole("button", { name: "Load Hours" }));
  expect(download.toggle).toHaveBeenCalledOnce();
  rerender(<ForecastDownloads download={{ ...download, running: true, started: true, loaded: 3, cached: 2 }} />);
  expect(screen.getByRole("progressbar", { name: "Downloaded Forecast Hours" }).getAttribute("value")).toBe("3");
  await user.click(screen.getByRole("button", { name: "Stop Download" }));
  expect(download.toggle).toHaveBeenCalledTimes(2);
  rerender(<ForecastDownloads download={{ ...download, complete: true, started: true, cached: 19, missing: [] }} />);
  expect(screen.queryByRole("button", { name: "Load Hours" })).toBeNull();
  expect(screen.getByRole("progressbar", { name: "Saved Forecast Hours" }).getAttribute("value")).toBe("19");
  rerender(<ForecastDownloads download={{ ...download, failed: true }} />);
  expect(screen.getByText("Some hours are unavailable for this run.")).toBeTruthy();
  expect(screen.queryByText(/Select a red hour/u)).toBeNull();
  rerender(<ForecastDownloads download={{ ...download, cacheError: true }} />);
  expect(screen.getByText(/Saved-hour cache unavailable/u)).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
  rerender(<ForecastDownloads download={{ ...download, disabled: true, total: 0, missing: [] }} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Load Hours" }).disabled).toBe(true);
});

it("marks cached, downloading and failed hours in the top-bar picker, retries one hour, and keeps bulk work when closed", async () => {
  const run = "2026-10-04T12:00:00Z";
  useForecastStore.setState({ modelKey: "hrrr", runTime: run, forecastHour: 0 });
  useMapStore.setState({ panelLayout: 1, productKey: "refc", layers: { ...mapInitial.layers, "model-field": { ...mapInitial.layers["model-field"], visible: true } } });
  const base = createTransport();
  const requests: (Readonly<Record<string, unknown>> | undefined)[] = [];
  const bytes = Uint8Array.from(atob(fieldBytes.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer;
  const { view, queryClient } = renderWithBackend(<><ForecastControls /><TimelineRegion /></>, {
    ...base, call: (command, args) => {
      if (command === "get_weather_field") { requests.push(args); return Promise.resolve(bytes); }
      return base.call(command, args);
    },
  });
  let finish!: (value: unknown) => void;
  try {
    await waitFor(() => { expect(queryClient.getQueryState(mapKeys.field("refc", run, 0, "hrrr"))?.status).toBe("success"); });
    const frame = queryClient.getQueryData(mapKeys.field("refc", run, 0, "hrrr"));
    await act(async () => {
      queryClient.setQueryData(mapKeys.field("refc", run, 1, "hrrr"), frame);
      void queryClient.query({ queryKey: mapKeys.field("refc", run, 2, "hrrr"), queryFn: () => new Promise((resolve) => { finish = resolve; }) }).catch(() => undefined);
      await queryClient.query({ queryKey: mapKeys.field("refc", run, 3, "hrrr"), queryFn: () => Promise.reject(new Error("temporary failure")) }).catch(() => undefined);
    });
    const timeline = screen.getByRole("region", { name: "Forecast timeline" });
    expect(within(timeline).queryByRole("button", { name: "Forecast Hours" })).toBeNull();
    expect(within(timeline).queryByText("Cached")).toBeNull();
    const user = userEvent.setup();
    const trigger = screen.getByRole("button", { name: "Choose forecast hour" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Forecast hour" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(within(dialog).getByRole("button", { name: "Select forecast hour 1" }).getAttribute("data-hour-state")).toBe("cached");
    expect(within(dialog).getByRole("button", { name: "Select forecast hour 2" }).getAttribute("data-hour-state")).toBe("loading");
    const failed = within(dialog).getByRole("button", { name: "Retry forecast hour 3" });
    expect(failed.getAttribute("data-hour-state")).toBe("failed");
    failed.focus();
    await user.keyboard("{Enter}");
    const cached = await within(dialog).findByRole("button", { name: "Select forecast hour 3" });
    await waitFor(() => { expect(cached.getAttribute("data-hour-state")).toBe("cached"); });
    expect(document.activeElement).toBe(cached);
    expect(cached.getAttribute("aria-pressed")).toBe("true");
    expect(requests.filter((request) => request?.forecastHour === 3)).toHaveLength(1);
    expect(useForecastStore.getState()).toMatchObject({ forecastHour: 3, playing: false });
    expect(screen.getByRole("slider", { name: "Forecast hour" }).getAttribute("aria-valuetext")).toContain("F003");
    await user.click(within(dialog).getByRole("button", { name: "Load Hours" }));
    await within(dialog).findByRole("button", { name: "Stop Download" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Stop Download" }));
    act(() => { finish(frame); });
    await waitFor(() => { expect(screen.queryByRole("button", { name: "Stop Download" })).toBeNull(); });
  } finally { view.unmount(); queryClient.clear(); }
});

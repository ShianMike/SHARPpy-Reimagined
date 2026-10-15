// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { MAX_LOG_ENTRIES, recordLog, startSessionLog, useLogStore } from "../../stores/logStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { useUiStore } from "../../stores/uiStore";
import { renderWithBackend } from "../../test/backendHarness";
import { SettingsView } from "../settings/SettingsView";
import { Toolbar } from "../toolbar/Toolbar";
import { LogsWorkspace } from "./LogsWorkspace";
import { OperationsWorkspace } from "./OperationsWorkspace";

const app = useAppStore.getState(), forecast = useForecastStore.getState(), sounding = useSoundingStore.getState(), ui = useUiStore.getState(), graphics = useGraphicsStore.getState();
beforeEach(() => {
  useAppStore.setState(app, true); useForecastStore.setState(forecast, true); useSoundingStore.setState(sounding, true); useUiStore.setState(ui, true); useGraphicsStore.setState(graphics, true);
  useLogStore.setState({ entries: [], nextId: 1 });
});
afterEach(cleanup);

it("resumes the current forecast and opens each sounding source directly", async () => {
  useForecastStore.getState().setRun("2024-02-29T12:00:00Z");
  useForecastStore.getState().setHour(6);
  renderWithBackend(<OperationsWorkspace />, createTransport());
  const current = await screen.findByRole("heading", { name: "HRRRF006" });
  expect(current).toBeTruthy();
  expect(screen.getByRole("region", { name: "On your map" }).textContent).toContain("02-29 18Z");
  const user = userEvent.setup();
  for (const [name, source] of [[/^Observed/u, "observed"], [/^ForecastA/u, "model"], [/^ERA5 archive/u, "era5"], [/^Open a file/u, "file"]] as const) {
    await user.click(screen.getByRole("button", { name }));
    expect(useSoundingStore.getState().source).toBe(source);
    expect(useAppStore.getState().activeWorkspace).toBe("sounding");
  }
  await user.click(screen.getByRole("button", { name: "Open map" }));
  expect(useAppStore.getState().activeWorkspace).toBe("map");
  expect(useForecastStore.getState()).toMatchObject({ runTime: "2024-02-29T12:00:00Z", forecastHour: 6 });
});

it("hides the whole map header outside map focus", () => {
  render(<Toolbar isMap={false}><button>Map detail views</button></Toolbar>);
  expect(screen.queryByRole("banner")).toBeNull();
  expect(screen.queryByText("SHARPpy")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});

it("records real client outcomes once and keeps repeated health polls quiet", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const base = createTransport();
  const client = new BackendClient({ ...base, call: (command, args) => command === "get_station_catalog"
    // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- backend contract is a plain object
    ? Promise.reject({ code: "CATALOG_UNAVAILABLE", message: "Catalogue could not be loaded", retryable: true, context: { operation: command }, details: { privateTrace: "not rendered" } }) : base.call(command, args) });
  await client.getBackendHealth(); await client.getBackendHealth();
  await client.getModelCatalog();
  await expect(client.getStationCatalog()).rejects.toThrow("Catalogue could not be loaded");
  const entries = useLogStore.getState().entries;
  expect(entries.filter((entry) => entry.source === "Backend")).toHaveLength(1);
  expect(entries.filter((entry) => entry.source === "get_model_catalog")).toHaveLength(1);
  expect(entries[0]).toMatchObject({ source: "get_station_catalog", level: "error", detail: "CATALOG_UNAVAILABLE" });
  expect(JSON.stringify(entries)).not.toContain("privateTrace");
});

it("filters errors and sources, expands details, and clears the session view", async () => {
  recordLog("info", "get_weather_field", "Request completed", "product: refc");
  recordLog("error", "open_sounding", "No profile available", "SOUNDING_UNAVAILABLE");
  renderWithBackend(<LogsWorkspace />, createTransport());
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Errors" }));
  expect(screen.getByRole("table").textContent).not.toContain("Request completed");
  await user.click(screen.getByText("No profile available", { selector: "summary span" }));
  expect(screen.getByText("SOUNDING_UNAVAILABLE").closest("details")?.open).toBe(true);
  await user.type(screen.getByRole("searchbox", { name: "Search logs" }), "missing");
  expect(screen.getByRole("heading", { name: "No matching events" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Reset filters" }));
  await user.type(screen.getByRole("searchbox", { name: "Search logs" }), "REFC");
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  await user.clear(screen.getByRole("searchbox", { name: "Search logs" }));
  await user.click(screen.getByRole("button", { name: "Clear session log" }));
  expect(screen.getByRole("heading", { name: "No events yet" })).toBeTruthy();
  act(() => { recordLog("warning", "Backend", "Connection interrupted"); });
  expect(screen.getByRole("table").textContent).toContain("Connection interrupted");
});

it("exports the filtered events and reports export failures", async () => {
  recordLog("error", "Application", "Test error");
  recordLog("info", "Application", "Test information");
  let exported: Blob | undefined;
  vi.stubGlobal("URL", class extends URL {
    static override createObjectURL(blob: Blob): string { exported = blob; return "blob:log-export"; }
    static override revokeObjectURL(): void { /* released after the download starts */ }
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  try {
    renderWithBackend(<LogsWorkspace />, createTransport());
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Errors" }));
    await user.click(screen.getByRole("button", { name: "Export view" }));
    expect(click).toHaveBeenCalledOnce();
    const text = await new Promise<string>((resolve) => { const reader = new FileReader(); reader.onload = () => { resolve(typeof reader.result === "string" ? reader.result : ""); }; reader.readAsText(exported as Blob); });
    expect(text).toContain("[ERROR] Application: Test error");
    expect(text).not.toContain("Test information");
    vi.spyOn(URL, "createObjectURL").mockImplementation(() => { throw new Error("No storage"); });
    await user.click(screen.getByRole("button", { name: "Export view" }));
    expect(screen.getByRole("alert").textContent).toContain("could not be exported");
  } finally { vi.unstubAllGlobals(); }
});

it("keeps a bounded log and captures application errors with removable listeners", () => {
  const stop = startSessionLog();
  window.dispatchEvent(new ErrorEvent("error", { message: "Render failed", filename: "app.js", lineno: 12 }));
  expect(useLogStore.getState().entries[0]).toMatchObject({ level: "error", message: "Render failed", detail: "app.js:12" });
  stop();
  window.dispatchEvent(new ErrorEvent("error", { message: "After cleanup" }));
  expect(useLogStore.getState().entries[0]?.message).toBe("Render failed");
  for (let i = 0; i < MAX_LOG_ENTRIES; i += 1) recordLog("info", "Workspace", String(i));
  expect(useLogStore.getState().entries).toHaveLength(MAX_LOG_ENTRIES);
  expect(useLogStore.getState().entries.at(-1)?.message).toBe("0");
  recordLog("error", "Application", "x".repeat(8000));
  expect(useLogStore.getState().entries[0]?.message).toHaveLength(4000);
});

it("keeps appearance, playback and graphics settings functional across sections", async () => {
  render(<SettingsView />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("radio", { name: /Dark/u }));
  expect(useUiStore.getState().theme).toBe("dark");
  await user.click(screen.getByRole("button", { name: "Forecast playback" }));
  await user.click(screen.getByRole("radio", { name: "2×" }));
  const wasLooping = useUiStore.getState().loop;
  await user.click(screen.getByRole("switch", { name: "Loop forecast animation" }));
  expect(useUiStore.getState()).toMatchObject({ speed: 2, loop: !wasLooping });
  await user.click(screen.getByRole("button", { name: "Map rendering" }));
  await user.click(screen.getByRole("radio", { name: /^Low/u }));
  expect(useGraphicsStore.getState().override).toBe("low");
  await user.click(screen.getByRole("button", { name: "Device diagnostics" }));
  expect(screen.getByTestId("capability-table")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Appearance" }));
  expect(screen.getByRole<HTMLInputElement>("radio", { name: /Dark/u }).checked).toBe(true);
  await waitFor(() => { expect(JSON.parse(localStorage.getItem("sharppy-workspace-ui") ?? "{}")).toMatchObject({ state: { theme: "dark", speed: 2 } }); });
  fireEvent.click(screen.getByRole("radio", { name: /System/u }));
});

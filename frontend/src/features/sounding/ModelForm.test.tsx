// @vitest-environment jsdom
import modelCatalog from "@contracts/public/soundings/models.catalog.json";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import type { Transport } from "../../lib/backend/transport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { renderWithBackend } from "../../test/backendHarness";
import { ModelForm, parseCoordinate } from "./ModelForm";

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  useSoundingStore.getState().setModel({ modelKey: "hrrr" });
});
afterEach(cleanup);

async function setup(catalog = modelCatalog) {
  const base = createTransport();
  const call = vi.fn<Transport["call"]>((command, args) => command === "get_model_catalog" ? Promise.resolve(catalog) : base.call(command, args));
  renderWithBackend(<ModelForm />, { ...base, call });
  const form = await screen.findByRole("form", { name: "Model sounding" });
  return { user: userEvent.setup(), call, form,
    latitude: screen.getByRole<HTMLInputElement>("textbox", { name: "Latitude" }),
    longitude: screen.getByRole<HTMLInputElement>("textbox", { name: "Longitude" }),
    submit: screen.getByRole<HTMLButtonElement>("button", { name: "Open sounding" }) };
}

it.each(["", " ", "NaN", "Infinity", "0x10", "0b10", "35,2", "1e2", "91", "-91"])("rejects invalid latitude %j", (text) => {
  expect(parseCoordinate(text, 90)).toBeNull();
});

it.each([[" 35.2 ", 90, 35.2], ["-180", 180, -180], ["+90", 90, 90], [".5", 90, 0.5]] as const)("accepts decimal coordinate %s", (text, limit, value) => {
  expect(parseCoordinate(text, limit)).toBe(value);
});

it("explains missing and invalid coordinates, links errors, and blocks invalid submission", async () => {
  const { user, form, latitude, longitude, submit, call } = await setup();
  expect(submit.disabled).toBe(true);
  expect(screen.queryByRole("alert")).toBeNull();
  await user.click(latitude); await user.tab();
  const error = screen.getByText("−90° to 90°").parentElement;
  expect(latitude.getAttribute("aria-describedby")).toBe(error?.id);
  expect(latitude.getAttribute("aria-invalid")).toBe("true");
  await user.type(latitude, "91"); await user.type(longitude, "181");
  expect(longitude.getAttribute("aria-invalid")).toBe("true");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(screen.getByText("−180° to 180°").parentElement?.id).toBe(longitude.getAttribute("aria-describedby"));
  fireEvent.submit(form);
  expect(call.mock.calls.some(([command]) => command === "open_sounding")).toBe(false);
  await user.clear(latitude); await user.type(latitude, "35");
  await user.clear(longitude); await user.type(longitude, "-97");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(submit.disabled).toBe(false);
});

it("rejects points beyond the selected model bounds before requesting a download", async () => {
  const { user, form, latitude, longitude, submit, call } = await setup();
  await user.type(latitude, "0"); await user.type(longitude, "0");
  expect(screen.getByRole("alert").textContent).toContain("HRRR covers CONUS");
  expect(submit.disabled).toBe(true);
  fireEvent.submit(form);
  expect(call.mock.calls.some(([command]) => command === "open_sounding")).toBe(false);
});

it("validates bounds that cross the date line without rejecting their western half", async () => {
  const catalog = structuredClone(modelCatalog);
  const hrrr = catalog.models.find((model) => model.key === "hrrr");
  if (hrrr === undefined) throw new Error("Missing HRRR fixture");
  hrrr.bounds = { west: 170, east: -170, south: -30, north: 30 };
  const { user, latitude, longitude, submit } = await setup(catalog);
  await user.type(latitude, "10"); await user.type(longitude, "-175");
  expect(submit.disabled).toBe(false);
  await user.clear(longitude); await user.type(longitude, "175");
  expect(submit.disabled).toBe(false);
  await user.clear(longitude); await user.type(longitude, "0");
  expect(submit.disabled).toBe(true);
});

it("falls back to an offered hour when the default exceeds the selected cycle's limit", async () => {
  useAppStore.setState({ selectedStationId: "72357" });
  const catalog = structuredClone(modelCatalog);
  const hrrr = catalog.models.find((model) => model.key === "hrrr");
  if (hrrr === undefined) throw new Error("Missing HRRR fixture");
  hrrr.forecastHours = [0, 1, 2]; hrrr.defaultForecastHour = 2;
  hrrr.maxForecastHours = Array.from({ length: 24 }, () => 1);
  useSoundingStore.getState().setModel({ forecastHour: 2 });
  const { user, latitude, longitude, submit, call } = await setup(catalog);
  expect(screen.queryByRole("switch")).toBeNull();
  expect(screen.getByRole("combobox", { name: "Forecast" }).textContent).toContain("F000");
  await user.click(screen.getByRole("combobox", { name: "Forecast" }));
  const popup = screen.getByRole("dialog", { name: "Forecast" });
  expect(within(popup).getAllByRole("option")).toHaveLength(2);
  expect(within(popup).queryByRole("option", { name: "F002" })).toBeNull();
  await user.keyboard("{ArrowRight}{Enter}");
  expect(useSoundingStore.getState().model.forecastHour).toBe(1);
  await user.click(screen.getByRole("combobox", { name: "Forecast" }));
  await user.click(screen.getByRole("option", { name: "F000" }));
  await user.type(latitude, "35"); await user.type(longitude, "-97");
  await user.click(submit);
  await waitFor(() => { expect(call.mock.calls.find(([command]) => command === "open_sounding")?.[1]?.request).toMatchObject({ forecastHour: 0, latitude: 35, longitude: -97, location: null }); });
});

it("searches sounding models without changing the selected value or the map forecast", async () => {
  const { user } = await setup({ ...modelCatalog, models: modelCatalog.models.filter((model) => ["hrrr", "rap", "aigfs"].includes(model.key)) });
  const mapForecast = useForecastStore.getState();
  const trigger = screen.getByRole("combobox", { name: "Model" });
  await user.click(trigger);
  const popup = screen.getByRole("dialog", { name: "Model" });
  expect(within(popup).getAllByRole("option").map((option) => option.getAttribute("value"))).toEqual(["hrrr", "rap"]);
  expect(within(popup).getByRole("group", { name: "Regional" })).toBeTruthy();
  expect(within(popup).queryByRole("option", { name: "AIGFS" })).toBeNull();
  const search = within(popup).getByRole("searchbox", { name: "Search models" });
  expect(document.activeElement).toBe(search);
  await user.type(search, "no matching model");
  expect(within(popup).getByRole("status").textContent).toBe("No matches.");
  expect(trigger.textContent).toBe("HRRR");
  expect(useSoundingStore.getState().model.modelKey).toBe("hrrr");
  await user.keyboard("{Escape}");
  expect(document.activeElement).toBe(trigger);
  await user.click(trigger);
  expect(screen.getByRole<HTMLInputElement>("searchbox").value).toBe("");
  await user.type(screen.getByRole("searchbox"), "RAP");
  await user.keyboard("{ArrowDown}{Enter}");
  expect(useSoundingStore.getState().model).toMatchObject({ modelKey: "rap", runTime: null, forecastHour: null });
  expect(useForecastStore.getState()).toBe(mapForecast);
  expect(document.activeElement).toBe(trigger);
});

it("keeps exact run timestamps behind the compact UTC cycle tiles", async () => {
  const run = modelCatalog.models.find((model) => model.key === "hrrr")?.recentRuns[1];
  if (run === undefined) throw new Error("Missing HRRR run fixture");
  const { user } = await setup();
  const trigger = screen.getByRole("combobox", { name: "Cycle (UTC)" });
  await user.click(trigger);
  const popup = screen.getByRole("dialog", { name: "Cycle (UTC)" });
  const choice = within(popup).getByRole("option", { name: `${run.slice(11, 13)}Z` });
  expect(choice.textContent).toContain(`${run.slice(11, 13)}Z`);
  expect(within(popup).getByRole("group", { name: `${run.slice(0, 10)} · UTC` })).toBeTruthy();
  await user.click(choice);
  expect(useSoundingStore.getState().model.runTime).toBe(run);
  expect(trigger.textContent).toBe(`${run.slice(11, 13)}Z`);
  expect(document.activeElement).toBe(trigger);
});

it("chooses a historical leap day in the custom calendar and sends its exact UTC cycle", async () => {
  useSoundingStore.getState().setModel({ runTime: "2024-03-31T12:00:00Z", forecastHour: 0 });
  const mapForecast = useForecastStore.getState();
  const { user, latitude, longitude, submit, call } = await setup();
  const date = screen.getByRole("button", { name: "Run date (UTC)" });
  await user.click(date);
  expect(screen.getByRole("dialog", { name: "Run date (UTC)" })).toBeTruthy();
  expect(screen.getByRole("grid", { name: "March 2024" })).toBeTruthy();
  await user.keyboard("{PageUp}{Enter}");
  expect(useSoundingStore.getState().model.runTime).toBe("2024-02-29T12:00:00Z");
  expect(date.textContent).toContain("29 Feb 2024");
  expect(screen.getByRole("combobox", { name: "Cycle (UTC)" }).textContent).toBe("12Z");
  expect(document.activeElement).toBe(date);
  expect(useForecastStore.getState()).toBe(mapForecast);
  await user.type(latitude, "35"); await user.type(longitude, "-97");
  await user.click(submit);
  await waitFor(() => { expect(call.mock.calls.find(([command]) => command === "open_sounding")?.[1]?.request)
    .toMatchObject({ model: "hrrr", runTime: "2024-02-29T12:00:00Z", forecastHour: 0, latitude: 35, longitude: -97 }); });
});

it("limits the newest UTC day to occurred cycles and clamps a later archived cycle", async () => {
  useSoundingStore.getState().setModel({ runTime: "2026-10-03T23:00:00Z" });
  const { user } = await setup();
  await user.click(screen.getByRole("button", { name: "Next run date" }));
  expect(useSoundingStore.getState().model.runTime).toBe("2026-10-04T18:00:00Z");
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Next run date" }).disabled).toBe(true);
  await user.click(screen.getByRole("combobox", { name: "Cycle (UTC)" }));
  const cycles = screen.getByRole("dialog", { name: "Cycle (UTC)" });
  expect(within(cycles).queryByRole("option", { name: "19Z" })).toBeNull();
  expect(within(cycles).getByRole("option", { name: "18Z" }).textContent).toContain("Pending");
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "Run date (UTC)" }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Monday, October 5, 2026" }).disabled).toBe(true);
});

it("can jump through months and years, cancel without changing the run, and return to the latest date", async () => {
  useSoundingStore.getState().setModel({ runTime: "2024-02-29T12:00:00Z" });
  const { user } = await setup();
  const trigger = screen.getByRole("button", { name: "Run date (UTC)" });
  await user.click(trigger);
  await user.click(screen.getByRole("button", { name: "Choose month and year" }));
  const year = screen.getByRole("spinbutton", { name: "Calendar year" });
  await user.clear(year); await user.type(year, "2025");
  await user.click(screen.getByRole("button", { name: "Jan" }));
  expect(screen.getByRole("grid", { name: "January 2025" })).toBeTruthy();
  await user.keyboard("{Escape}");
  expect(useSoundingStore.getState().model.runTime).toBe("2024-02-29T12:00:00Z");
  expect(document.activeElement).toBe(trigger);
  await user.click(trigger);
  await user.click(screen.getByRole("button", { name: "Latest date" }));
  expect(useSoundingStore.getState().model.runTime).toBe("2026-10-04T12:00:00Z");
});

it("uses the selected model's date and cycle schedule after changing models", async () => {
  useSoundingStore.getState().setModel({ runTime: "2024-02-29T23:00:00Z" });
  const { user } = await setup();
  await user.click(screen.getByRole("combobox", { name: "Model" }));
  await user.type(screen.getByRole("searchbox", { name: "Search models" }), "GFS");
  await user.click(screen.getByRole("option", { name: "GFS" }));
  expect(screen.getByRole("button", { name: "Run date (UTC)" }).textContent).toContain("4 Oct 2026");
  expect(screen.getByRole("combobox", { name: "Cycle (UTC)" }).textContent).toBe("12Z");
  await user.click(screen.getByRole("combobox", { name: "Cycle (UTC)" }));
  expect(within(screen.getByRole("dialog", { name: "Cycle (UTC)" })).getAllByRole("option")
    .map((option) => option.getAttribute("aria-label"))).toEqual(["00Z", "06Z", "12Z", "18Z"]);
});

it("recomputes forecast-hour choices when a selected cycle has a shorter range", async () => {
  useSoundingStore.getState().setModel({ runTime: "2026-10-03T12:00:00Z", forecastHour: 47 });
  const { user } = await setup();
  expect(screen.getByRole("combobox", { name: "Forecast" }).textContent).toBe("F047");
  await user.click(screen.getByRole("combobox", { name: "Cycle (UTC)" }));
  await user.click(screen.getByRole("option", { name: "13Z" }));
  expect(screen.getByRole("combobox", { name: "Forecast" }).textContent).toBe("F000");
  await user.click(screen.getByRole("combobox", { name: "Forecast" }));
  const popup = screen.getByRole("dialog", { name: "Forecast" });
  expect(within(popup).queryByRole("option", { name: "F047" })).toBeNull();
  expect(within(popup).getByRole("option", { name: "F018" })).toBeTruthy();
});

it("preserves signed map longitude while requesting the same geographic point", async () => {
  useSoundingStore.getState().setModel({ latitude: "32.9604", longitude: "-96.6463" });
  const { user, longitude, submit, call } = await setup();
  expect(longitude.value).toBe("-96.6463");
  expect(screen.getByText("°W")).toBeTruthy();
  expect(screen.getByText("Negative = south / west")).toBeTruthy();
  await user.click(submit);
  await waitFor(() => { expect(call.mock.calls.find(([command]) => command === "open_sounding")?.[1]?.request)
    .toMatchObject({ latitude: 32.9604, longitude: -96.6463 }); });
});

it("keeps coordinate signs as entered and follows both hemispheres in the labels", async () => {
  const { user, latitude, longitude, submit } = await setup();
  await user.type(latitude, "35.2"); await user.type(longitude, "97.4");
  expect(latitude.value).toBe("35.2"); expect(longitude.value).toBe("97.4");
  expect(screen.getByText("°N")).toBeTruthy(); expect(screen.getByText("°E")).toBeTruthy();
  expect(submit.disabled).toBe(true);
  fireEvent.change(latitude, { target: { value: "-35.2" } });
  fireEvent.change(longitude, { target: { value: "-97.4" } });
  expect(latitude.value).toBe("-35.2"); expect(longitude.value).toBe("-97.4");
  expect(screen.getByText("°S")).toBeTruthy(); expect(screen.getByText("°W")).toBeTruthy();
  fireEvent.change(latitude, { target: { value: "+35.2" } });
  fireEvent.change(longitude, { target: { value: "+97.4" } });
  expect(latitude.value).toBe("+35.2"); expect(longitude.value).toBe("+97.4");
  expect(screen.getByText("°N")).toBeTruthy(); expect(screen.getByText("°E")).toBeTruthy();
});

it("accepts positive eastern longitude on global grids without fixing malformed signs into valid requests", async () => {
  useSoundingStore.getState().setModel({ modelKey: "gfs" });
  const { user, form, latitude, longitude, submit, call } = await setup();
  await user.type(latitude, "35"); await user.type(longitude, "151.5");
  expect(longitude.value).toBe("151.5");
  expect(screen.getByText("°E")).toBeTruthy();
  expect(submit.disabled).toBe(false);
  await user.click(submit);
  await waitFor(() => { expect(call.mock.calls.find(([command]) => command === "open_sounding")?.[1]?.request)
    .toMatchObject({ model: "gfs", longitude: 151.5 }); });
  await waitFor(() => { expect(submit.disabled).toBe(false); });
  for (const invalid of ["--1", "++1", "-+1", "+-1", "- 1"]) {
    await user.clear(longitude); await user.type(longitude, invalid);
    expect(longitude.getAttribute("aria-invalid")).toBe("true");
    expect(submit.disabled).toBe(true);
    fireEvent.submit(form);
    expect(call.mock.calls.filter(([command]) => command === "open_sounding")).toHaveLength(1);
  }
});

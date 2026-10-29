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
import { formatUtc } from "./soundingTime";

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
  useSoundingStore.getState().setModel({ modelKey: "hrrr", atStation: false });
});
afterEach(cleanup);

async function setup(catalog = modelCatalog) {
  const base = createTransport();
  const call = vi.fn<Transport["call"]>((command, args) => command === "get_model_catalog" ? Promise.resolve(catalog) : base.call(command, args));
  renderWithBackend(<ModelForm />, { ...base, call });
  const form = await screen.findByRole("form", { name: "Model sounding" });
  return { user: userEvent.setup(), call, form,
    latitude: screen.getByRole<HTMLInputElement>("textbox", { name: "Latitude, degrees north" }),
    longitude: screen.getByRole<HTMLInputElement>("textbox", { name: "Longitude, degrees east" }),
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
  const catalog = structuredClone(modelCatalog);
  const hrrr = catalog.models.find((model) => model.key === "hrrr");
  if (hrrr === undefined) throw new Error("Missing HRRR fixture");
  hrrr.forecastHours = [0, 1, 2]; hrrr.defaultForecastHour = 2;
  hrrr.maxForecastHours = Array.from({ length: 24 }, () => 1);
  useSoundingStore.getState().setModel({ forecastHour: 2 });
  const { user, latitude, longitude, submit, call } = await setup(catalog);
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
  await waitFor(() => { expect(call.mock.calls.find(([command]) => command === "open_sounding")?.[1]?.request).toMatchObject({ forecastHour: 0 }); });
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
  const trigger = screen.getByRole("combobox", { name: "Run" });
  await user.click(trigger);
  const popup = screen.getByRole("dialog", { name: "Run" });
  const choice = within(popup).getByRole("option", { name: formatUtc(run) });
  expect(choice.textContent).toContain(`${run.slice(11, 13)}Z`);
  expect(within(popup).getByRole("group", { name: `${run.slice(0, 10)} · UTC` })).toBeTruthy();
  await user.click(choice);
  expect(useSoundingStore.getState().model.runTime).toBe(run);
  expect(trigger.textContent).toBe(formatUtc(run));
  expect(document.activeElement).toBe(trigger);
});

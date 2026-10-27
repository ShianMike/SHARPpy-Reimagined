// @vitest-environment jsdom
import mapCatalog from "@contracts/public/map/catalog.json";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseMapCatalog } from "../../lib/contracts/map";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { panelProduct, useMapStore } from "../../stores/mapStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { renderWithBackend } from "../../test/backendHarness";
import { Inspector } from "../inspector/Inspector";
import { ModelForm } from "../sounding/ModelForm";
import { TimelineRegion } from "../timeline/TimelineRegion";
import { ForecastControls } from "./ForecastControls";

const forecastInitial = useForecastStore.getState();
const appInitial = useAppStore.getState();
const mapInitial = useMapStore.getState();
const soundingInitial = useSoundingStore.getState();
beforeEach(() => {
  useForecastStore.setState(forecastInitial, true);
  useAppStore.setState(appInitial, true);
  useMapStore.setState(mapInitial, true);
  useSoundingStore.setState(soundingInitial, true);
});
afterEach(cleanup);

it("keeps Winter Weather last and separates the description into a second glass card", async () => {
  renderWithBackend(<ForecastControls />, createTransport("field-error"));
  const trigger = screen.getByRole<HTMLButtonElement>("button", { name: "Choose parameter" });
  await waitFor(() => { expect(trigger.disabled).toBe(false); });
  await userEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Parameters" });
  const browser = within(dialog).getByRole("region", { name: "Parameter browser" });
  const description = within(dialog).getByRole("region", { name: "Parameter description" });
  const categories = within(browser).getByRole("navigation", { name: "Parameter categories" });
  const buttons = within(categories).getAllByRole("button");
  expect(buttons).toHaveLength(10);
  expect(buttons.at(-1)?.textContent).toBe("Winter Weather4");
  expect(browser.contains(description)).toBe(false);
  expect(browser.parentElement).toBe(description.parentElement);
  expect(browser.getAttribute("data-surface")).toBe("glass");
  expect(description.getAttribute("data-surface")).toBe("glass");
  await userEvent.click(within(categories).getByRole("button", { name: /^Winter Weather/u }));
  expect(within(browser).getByRole("region", { name: "Parameter results" }).querySelectorAll("button")).toHaveLength(4);
  await userEvent.click(description);
  expect(screen.getByRole("dialog", { name: "Parameters" })).toBe(dialog);
  await userEvent.keyboard("{Escape}");
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Parameters" })).toBeNull(); });
  expect(document.activeElement).toBe(trigger);
});

it("reports an incompatible map catalogue and recovers after the backend is updated", async () => {
  const incompatible = structuredClone(mapCatalog);
  for (const product of incompatible.products) Reflect.deleteProperty(product.palette, "display");
  const base = createTransport("field-error");
  let staleBackend = true;
  renderWithBackend(<ForecastControls />, {
    ...base, call: (command, args) => command === "get_map_catalog" && staleBackend
      ? Promise.resolve(incompatible) : base.call(command, args),
  });
  const dialog = await screen.findByRole("dialog", { name: "Parameters unavailable" });
  expect(dialog.parentElement).toBe(document.body);
  expect(within(screen.getByRole("group", { name: "Forecast" })).queryByRole("button", { name: "Retry" })).toBeNull();
  const parameter = screen.getByRole<HTMLButtonElement>("button", { name: "Choose parameter" });
  expect(parameter.textContent).toContain("Unavailable");
  expect(parameter.getAttribute("aria-description")).toContain("versions may not match");
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Choose model" }).disabled).toBe(false);
  await userEvent.click(within(dialog).getByRole("button", { name: "Dismiss" }));
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Parameters unavailable" })).toBeNull(); });
  expect(document.activeElement).toBe(parameter);
  await userEvent.click(parameter);
  const reopened = screen.getByRole("dialog", { name: "Parameters unavailable" });
  staleBackend = false;
  await userEvent.click(within(reopened).getByRole("button", { name: "Retry" }));
  await waitFor(() => { expect(parameter.disabled).toBe(false); });
  expect(parameter.textContent).toContain("Composite Reflectivity");
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Parameters unavailable" })).toBeNull(); });
});

async function chooseDate(date: string): Promise<void> {
  await userEvent.click(screen.getByRole("button", { name: "Run date · UTC" }));
  await userEvent.click(screen.getByRole("button", { name: "Choose month and year" }));
  fireEvent.change(screen.getByRole("spinbutton", { name: "Calendar year" }), { target: { value: date.slice(0, 4) } });
  const moment = new Date(`${date}T00:00:00Z`);
  await userEvent.click(screen.getByRole("button", { name: new Intl.DateTimeFormat("en", { month: "short", timeZone: "UTC" }).format(moment) }));
  await userEvent.click(screen.getByRole("button", { name: new Intl.DateTimeFormat("en", { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }).format(moment) }));
}

it("sends an archived UTC run from the date picker through the map, timeline, and point sounding", async () => {
  useMapStore.getState().setLayerVisible("model-field", true);
  useAppStore.getState().selectPoint({ latitude: 35, longitude: -97 });
  const base = createTransport("field-error");
  const fields: unknown[] = [];
  let sounding: unknown;
  renderWithBackend(<><ForecastControls /><TimelineRegion /><Inspector /><ModelForm /></>, {
    ...base, call: (command, args) => {
      if (command === "get_weather_field") fields.push(args);
      if (command === "open_sounding") sounding = args;
      return base.call(command, args);
    },
  });
  await waitFor(() => { expect(screen.getByRole<HTMLButtonElement>("button", { name: "Choose run" }).disabled).toBe(false); });
  await userEvent.click(screen.getByRole("button", { name: "Choose run" }));
  const date = screen.getByLabelText("Run date · UTC");
  expect(document.activeElement).toBe(date);
  await chooseDate("2024-02-29");
  await userEvent.click(screen.getByRole("button", { name: "Run 02-29 12Z" }));
  expect(useForecastStore.getState().runTime).toBe("2024-02-29T12:00:00Z");
  expect(screen.getByRole("button", { name: "Choose run" }).textContent).toContain("2024");
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Choose run" }));
  await userEvent.click(screen.getByRole("button", { name: "Choose forecast hour" }));
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Select forecast hour 0" }));
  await userEvent.click(within(screen.getByRole("region", { name: "Valid 2024-03-02" })).getByRole("button", { name: "Select forecast hour 40" }));
  expect(screen.getByLabelText("Valid time").textContent).toContain("03-02 04Z");
  await waitFor(() => { expect(fields).toContainEqual(expect.objectContaining({ runTime: "2024-02-29T12:00:00Z", forecastHour: 40 })); });
  await userEvent.click(screen.getByRole("button", { name: "Open HRRR sounding here" }));
  expect(screen.getByRole<HTMLButtonElement>("combobox", { name: "Run" }).value).toBe("2024-02-29T12:00:00Z");
  await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
  await waitFor(() => { expect(sounding).toMatchObject({ request: { model: "hrrr", runTime: "2024-02-29T12:00:00Z", forecastHour: 40 } }); });
});

it("keeps run edits when the sounding form starts with the default model", async () => {
  renderWithBackend(<ModelForm />, createTransport());
  const run = await screen.findByRole<HTMLButtonElement>("combobox", { name: "Run" });
  await userEvent.click(run);
  await userEvent.click(screen.getByRole("option", { name: "2026-10-04 12Z" }));
  expect(run.value).toBe("2026-10-04T12:00:00Z");
});

it("disables future dates and cycles, crosses day boundaries, and returns to the latest run", async () => {
  renderWithBackend(<ForecastControls />, createTransport());
  await waitFor(() => { expect(screen.getByRole<HTMLButtonElement>("button", { name: "Choose run" }).disabled).toBe(false); });
  await userEvent.click(screen.getByRole("button", { name: "Choose run" }));
  const date = screen.getByRole("button", { name: "Run date · UTC" });
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Run 10-04 23Z" }).disabled).toBe(true);
  await userEvent.click(date);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Monday, October 5, 2026" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Next month" }).disabled).toBe(true);
  await userEvent.keyboard("{Escape}");
  expect(document.activeElement).toBe(date);
  expect(screen.getByRole("dialog", { name: "Model run" })).toBeTruthy();
  await chooseDate("2024-03-01");
  await userEvent.click(screen.getByRole("button", { name: "Previous run date" }));
  expect(date.textContent).toContain("29 Feb 2024");
  await userEvent.click(screen.getByRole("button", { name: "Next run date" }));
  expect(date.textContent).toContain("1 Mar 2024");
  await userEvent.click(screen.getByRole("button", { name: "Use latest suggested run" }));
  expect(useForecastStore.getState().runTime).toBe("2026-10-04T16:00:00Z");
  expect(screen.queryByRole("dialog")).toBeNull();
});

const exponentProducts = parseMapCatalog(mapCatalog).products.filter((product) => product.palette.units.includes("^"));
it.each(["tmp-2m", "rh-2m", "ltng", ...exponentProducts.map((product) => product.key)])("filters %s, previews keyboard-focused descriptions, and changes only the active panel", async (key) => {
  const product = parseMapCatalog(mapCatalog).products.find((item) => item.key === key);
  if (product === undefined) throw new Error("Missing parameter fixture");
  useMapStore.setState({ panelLayout: 2, activePanel: 1 });
  const firstPanel = panelProduct(useMapStore.getState(), 0);
  renderWithBackend(<ForecastControls />, createTransport("field-error"));
  await waitFor(() => { expect(screen.getByRole<HTMLButtonElement>("button", { name: "Choose parameter" }).disabled).toBe(false); });
  await userEvent.click(screen.getByRole("button", { name: "Choose parameter" }));
  const search = screen.getByRole("searchbox", { name: "Search parameters" });
  expect(document.activeElement).toBe(search);
  await userEvent.type(search, "no-such-parameter");
  await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));
  await userEvent.click(within(screen.getByRole("navigation", { name: "Parameter categories" })).getByText(product.category, { exact: true }));
  const unit = product.palette.units === "m^2/s^2" ? "m²/s²" : product.palette.units;
  const choice = screen.getByRole("button", { name: `${product.label}, ${unit}` });
  expect(choice.textContent).toContain(unit);
  act(() => { choice.focus(); });
  expect(document.getElementById("parameter-description")?.textContent).toContain(product.description);
  expect(document.getElementById("parameter-description")?.textContent).toContain(unit);
  await userEvent.keyboard("{Enter}");
  expect(panelProduct(useMapStore.getState(), 1)).toBe(product.key);
  expect(panelProduct(useMapStore.getState(), 0)).toBe(firstPanel);
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Choose parameter" }));
});

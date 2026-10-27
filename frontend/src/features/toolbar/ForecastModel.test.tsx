// @vitest-environment jsdom
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useForecastStore } from "../../stores/forecastStore";
import { renderWithBackend } from "../../test/backendHarness";
import { ForecastControls } from "./ForecastControls";

const initial = useForecastStore.getState();
beforeEach(() => {
  useForecastStore.setState(initial, true);
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

async function openModels() {
  renderWithBackend(<ForecastControls />, createTransport());
  const trigger = await screen.findByRole<HTMLButtonElement>("button", { name: "Choose model" });
  await waitFor(() => { expect(trigger.disabled).toBe(false); });
  await userEvent.click(trigger);
  return screen.getByRole("dialog", { name: "Forecast model" });
}

it("opens the selected model's region and keeps selection visible while browsing other regions", async () => {
  useForecastStore.getState().setModel("ecmwf-ifs-9km");
  const dialog = await openModels();
  expect(within(dialog).getByRole<HTMLInputElement>("radio", { name: "Global" }).checked).toBe(true);
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(5);
  expect(within(dialog).getByRole("button", { name: "Choose AIGFS" }).textContent).toContain("Maps only");
  expect(within(dialog).getByRole("button", { name: "Choose ECMWF 9km" }).textContent).toContain("Selected");
  expect(dialog.querySelectorAll("svg")).toHaveLength(2);
  expect(within(dialog).getByRole("button", { name: "Move popup" }).querySelector("svg")).not.toBeNull();
  expect(within(dialog).getByRole("button", { name: "Close forecast setup" }).querySelector("svg")).not.toBeNull();
  await userEvent.click(within(dialog).getByRole("radio", { name: "CONUS" }));
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(11);
  expect(dialog.querySelector("footer")?.textContent).toContain("ECMWF 9km");
  expect(useForecastStore.getState().modelKey).toBe("ecmwf-ifs-9km");
});

it.each([["NAM", "nam"], ["RAP", "rap"]])("opens %s in Regional and keeps NAM 3km in CONUS", async (label, key) => {
  useForecastStore.getState().setModel(key);
  const dialog = await openModels();
  expect(within(dialog).getByRole<HTMLInputElement>("radio", { name: "Regional" }).checked).toBe(true);
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(3);
  expect(within(dialog).getByRole("button", { name: `Choose ${label}` }).textContent).toContain("Selected");
  await userEvent.click(within(dialog).getByRole("radio", { name: "CONUS" }));
  expect(within(dialog).queryByRole("button", { name: "Choose NAM" })).toBeNull();
  expect(within(dialog).queryByRole("button", { name: "Choose RAP" })).toBeNull();
  expect(within(dialog).getByRole("button", { name: "Choose NAM 3km CONUS" })).toBeTruthy();
});

it.each([["ECMWF", "openmeteo-ecmwf-ifs025"], ["GDPS", "gdps"], ["AIGFS", "aigfs"]])("switches regions and selects %s without extra confirmation", async (label, key) => {
  const dialog = await openModels();
  await userEvent.click(within(dialog).getByRole("radio", { name: "CONUS" }));
  await userEvent.click(within(dialog).getByRole("radio", { name: "Global" }));
  expect(within(dialog).getByRole<HTMLInputElement>("radio", { name: "Global" }).checked).toBe(true);
  expect(within(dialog).queryByRole("button", { name: "Choose HRRR" })).toBeNull();
  await userEvent.click(within(dialog).getByRole("button", { name: `Choose ${label}` }));
  expect(useForecastStore.getState().modelKey).toBe(key);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Choose model" }));
});

it("searches across regions and makes an empty search easy to clear", async () => {
  const dialog = await openModels();
  const search = within(dialog).getByRole<HTMLInputElement>("searchbox", { name: "Search models" });
  await userEvent.type(search, "ecmwf");
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(2);
  expect(within(dialog).getByRole("group", { name: "Global" })).toBeTruthy();
  expect(within(dialog).queryByRole("group", { name: "CONUS" })).toBeNull();
  await userEvent.click(within(dialog).getByRole("button", { name: "Clear search" }));
  expect(search.value).toBe("");
  expect(document.activeElement).toBe(search);
  expect(within(dialog).getByRole("button", { name: "Choose HRRR" })).toBeTruthy();
  await userEvent.type(search, "no-such-model");
  expect(within(dialog).getByText("No models found.")).toBeTruthy();
  await userEvent.click(within(dialog).getByRole("radio", { name: "Global" }));
  expect(search.value).toBe("");
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(5);
});

it("preserves the selected run and hour when reselecting the current model", async () => {
  useForecastStore.getState().setModel("hrrr");
  useForecastStore.getState().setRun("2026-10-04T12:00:00Z");
  useForecastStore.getState().setHour(12);
  const dialog = await openModels();
  await userEvent.click(within(dialog).getByRole("button", { name: "Choose HRRR" }));
  expect(useForecastStore.getState()).toMatchObject({ modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 12 });
});

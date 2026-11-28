// @vitest-environment jsdom
import mapCatalog from "@contracts/public/map/catalog.json";
import modelCatalog from "@contracts/public/soundings/models.catalog.json";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useForecastStore } from "../../stores/forecastStore";
import { renderWithBackend } from "../../test/backendHarness";
import { NAM_ARCHIVE_NOTICE } from "../sounding/soundingQueries";
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
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(6);
  expect(within(dialog).getByRole("button", { name: "Choose AIGFS" }).textContent).toContain("Maps only");
  expect(within(dialog).getByRole("button", { name: "Choose ECMWF 9km", pressed: true })).toBeTruthy();
  expect(within(dialog).getByRole("button", { name: "Move popup" }).querySelector("svg")).not.toBeNull();
  expect(within(dialog).getByRole("button", { name: "Close forecast setup" }).querySelector("svg")).not.toBeNull();
  await userEvent.click(within(dialog).getByRole("radio", { name: "CONUS" }));
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(4);
  expect(dialog.querySelector("footer")?.textContent).toContain("ECMWF 9km");
  expect(useForecastStore.getState().modelKey).toBe("ecmwf-ifs-9km");
});

it.each([
  { key: "nssl-mpas-htpo", label: "NSSL MPAS-HTPO", legacyKey: "hrw-wrf-arw", unavailableKey: "nssl-mpas-rn", unavailableLabel: "NSSL MPAS-RN" },
  { key: "nssl-mpas-rn", label: "NSSL MPAS-RN", legacyKey: "hrw-wrf-nssl", unavailableKey: "nssl-mpas-htpo", unavailableLabel: "NSSL MPAS-HTPO" },
])("offers $label with published runs and hides all retired HRW models", async ({ key, label, legacyKey, unavailableKey, unavailableLabel }) => {
  const legacy = modelCatalog.models.find((model) => model.key === legacyKey);
  if (legacy === undefined) throw new Error("HRW fixture missing");
  const suggestedRun = "2026-11-24T00:00:00Z";
  const olderRun = "2026-11-21T12:00:00Z";
  const scheduledButUnlisted = "2026-11-22T12:00:00Z";
  const notes = "MPAS runs may reach the archive several days after initialization.";
  const mpas = {
    ...legacy, key, label, soundingsAvailable: false, gridSpacingKm: 3, notes,
    cycles: [0, 12], defaultForecastHour: 1, recentRuns: [suggestedRun, olderRun], suggestedRun,
  };
  const existingModels = modelCatalog.models.filter((model) => model.key !== key && model.key !== unavailableKey);
  const base = createTransport("field-error");
  const call = vi.fn<typeof base.call>((command, args) => {
    if (command === "get_model_catalog") return Promise.resolve({
      ...modelCatalog, models: [...existingModels, mpas, { ...mpas, key: unavailableKey, label: unavailableLabel, suggestedRun: scheduledButUnlisted }],
    });
    if (command === "get_map_catalog" && args?.model === key) return Promise.resolve({
      ...mapCatalog, fieldModel: key, defaultProduct: "tmp-2m",
      products: mapCatalog.products.filter((product) => product.key === "tmp-2m"),
    });
    return base.call(command, args);
  });
  useForecastStore.setState({ modelKey: legacyKey, runTime: legacy.suggestedRun, forecastHour: 12 });
  const { queryClient } = renderWithBackend(<ForecastControls />, { ...base, call });
  const chooseModel = await screen.findByRole<HTMLButtonElement>("button", { name: "Choose model" });
  await waitFor(() => {
    expect(chooseModel.disabled).toBe(false);
    expect(chooseModel.textContent).toContain("HRRR");
  });
  await userEvent.click(chooseModel);
  const dialog = within(screen.getByRole("dialog", { name: "Forecast model" }));
  const conus = within(dialog.getByRole("group", { name: "CONUS" }));
  const choice = conus.getByRole("button", { name: `Choose ${label}` });
  expect(choice.textContent).toContain("Maps only");
  expect(choice.textContent).toContain("3 km");
  expect(conus.queryAllByRole("button", { name: /^Choose (?:HRW|HiResW)/iu })).toHaveLength(0);
  expect(conus.getAllByRole("button", { name: /^Choose NSSL MPAS/u })).toEqual([choice]);

  await userEvent.click(choice);
  await waitFor(() => {
    expect(call).toHaveBeenCalledWith("get_map_catalog", { model: key, runTime: suggestedRun, forecastHour: 1 });
  });
  expect(useForecastStore.getState()).toMatchObject({ modelKey: key, runTime: null, forecastHour: null });
  await userEvent.click(screen.getByRole("button", { name: "Choose run" }));
  const runDialog = within(screen.getByRole("dialog", { name: "Model run" }));
  expect(runDialog.getByText(notes)).toBeTruthy();
  expect(runDialog.getByRole<HTMLButtonElement>("button", { name: "Run 11-24 12Z" }).disabled).toBe(true);
  await userEvent.click(runDialog.getByRole("button", { name: "Previous run date" }));
  await userEvent.click(runDialog.getByRole("button", { name: "Run 11-21 12Z" }));
  await waitFor(() => {
    expect(call).toHaveBeenCalledWith("get_map_catalog", { model: key, runTime: olderRun, forecastHour: 1 });
  });
  queryClient.clear();
});

it("groups the REFS mean and members under Ensemble and reopens on the selection", async () => {
  const dialog = await openModels();
  expect(within(dialog).queryByRole("button", { name: "Choose REFSm1" })).toBeNull();
  const search = within(dialog).getByRole("searchbox", { name: "Search models" });
  await userEvent.type(search, "ensemble");
  const group = within(dialog).getByRole("group", { name: "Ensemble" });
  expect(within(group).getAllByRole("button").map((button) => button.getAttribute("aria-label")))
    .toEqual(["Choose REFS Mean", "Choose REFSm1", "Choose REFSm2", "Choose REFSm3", "Choose REFSm4", "Choose REFSm5"]);
  const mean = within(group).getByRole("button", { name: "Choose REFS Mean" });
  expect(mean.textContent).toContain("Maps only");
  await userEvent.click(mean);
  expect(useForecastStore.getState().modelKey).toBe("refs");
  await userEvent.click(screen.getByRole("button", { name: "Choose model" }));
  const reopened = screen.getByRole("dialog", { name: "Forecast model" });
  expect(within(reopened).getByRole<HTMLInputElement>("radio", { name: "Ensemble" }).checked).toBe(true);
  expect(within(reopened).getByRole("button", { name: "Choose REFS Mean" }).getAttribute("aria-pressed")).toBe("true");
});

it("opens RAP in Regional and labels the NAM archives in CONUS", async () => {
  useForecastStore.getState().setModel("rap");
  const dialog = await openModels();
  expect(within(dialog).getByRole<HTMLInputElement>("radio", { name: "Regional" }).checked).toBe(true);
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(3);
  expect(within(dialog).getByRole("button", { name: "Choose RAP", pressed: true })).toBeTruthy();
  expect(within(dialog).getByRole("button", { name: "Choose HRDPS" }).textContent).toContain("Maps only");
  expect(within(dialog).queryByText(NAM_ARCHIVE_NOTICE)).toBeNull();
  expect(within(dialog).queryByText("Archive", { exact: true })).toBeNull();
  await userEvent.click(within(dialog).getByRole("radio", { name: "CONUS" }));
  expect(within(dialog).queryByRole("button", { name: "Choose RAP" })).toBeNull();
  const conus = within(dialog).getByRole("group", { name: "CONUS" });
  for (const name of ["Choose NAM", "Choose NAM 3km CONUS"]) {
    const choice = within(conus).getByRole("button", { name, description: NAM_ARCHIVE_NOTICE });
    expect(within(choice).getByText("Archive", { exact: true })).toBeTruthy();
  }
  expect(within(conus).getAllByText("Archive", { exact: true })).toHaveLength(2);
  expect(within(dialog).getByText(NAM_ARCHIVE_NOTICE)).toBeTruthy();
});

it.each(["nam", "nam-3km-conus"])("shows the last archived run and explains unavailable %s cycles", async (modelKey) => {
  useForecastStore.getState().setModel(modelKey);
  renderWithBackend(<ForecastControls />, createTransport());
  const trigger = await screen.findByRole<HTMLButtonElement>("button", { name: "Choose run" });
  await waitFor(() => { expect(trigger.disabled).toBe(false); });
  await userEvent.click(trigger);

  const dialog = within(screen.getByRole("dialog", { name: "Model run" }));
  expect(dialog.getByText(NAM_ARCHIVE_NOTICE)).toBeTruthy();
  const date = dialog.getByRole("button", { name: "Run date · UTC", description: NAM_ARCHIVE_NOTICE });
  expect(date.textContent).toContain("3 Nov 2026");
  const unavailable = dialog.getByRole<HTMLButtonElement>("button", { name: "Run 11-03 12Z" });
  expect(unavailable.disabled).toBe(true);
  expect(unavailable.title).toBe("Cycle unavailable");
  expect(dialog.getByRole<HTMLButtonElement>("button", { name: "Run 11-03 06Z", pressed: true }).disabled).toBe(false);
  const latest = dialog.getByRole("button", { name: "Use latest archived run" });
  expect(latest.textContent).toContain("11-03 06Z");
  await userEvent.click(latest);
  expect(useForecastStore.getState().runTime).toBe("2026-11-03T06:00:00Z");
});

it.each([["ICON", "openmeteo-icon-global"], ["ECMWF", "openmeteo-ecmwf-ifs025"], ["GDPS", "gdps"], ["AIGFS", "aigfs"]])("switches regions and selects %s without extra confirmation", async (label, key) => {
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
  expect(within(dialog).getAllByRole("button", { name: /^Choose /u })).toHaveLength(6);
  expect(within(dialog).queryByText(NAM_ARCHIVE_NOTICE)).toBeNull();
  await userEvent.type(search, "nam");
  const conus = within(dialog).getByRole("group", { name: "CONUS" });
  expect(within(conus).getAllByRole("button").map((button) => button.getAttribute("aria-label")))
    .toEqual(["Choose NAM", "Choose NAM 3km CONUS"]);
  expect(within(conus).getAllByText("Archive", { exact: true })).toHaveLength(2);
  expect(within(dialog).getByText(NAM_ARCHIVE_NOTICE)).toBeTruthy();
});

it("preserves the selected run and hour when reselecting the current model", async () => {
  useForecastStore.getState().setModel("hrrr");
  useForecastStore.getState().setRun("2026-10-04T12:00:00Z");
  useForecastStore.getState().setHour(12);
  const dialog = await openModels();
  await userEvent.click(within(dialog).getByRole("button", { name: "Choose HRRR" }));
  expect(useForecastStore.getState()).toMatchObject({ modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 12 });
});

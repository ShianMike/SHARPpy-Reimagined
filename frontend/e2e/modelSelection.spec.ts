import { waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";

const archiveModels = ["NAM", "NAM 3km CONUS"] as const;
const archiveNotice = /3 Nov 2026.*06Z/u;
const retiredFamilies = ["SREF", "HREF", "HRW", "HiResW"];

test("shows the two NAM archives in both selectors in dark and light themes", async ({ page, consoleMessages }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/");
  const workspaces = page.getByRole("navigation", { name: "Workspaces" });
  for (const theme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await workspaces.getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMapDrawn(page);
    await page.getByRole("button", { name: "Choose model", exact: true }).click();
    const forecast = page.getByRole("dialog", { name: "Forecast model" });
    for (const [region, supported] of [
      ["CONUS", ["HRRR", "RRFS", ...archiveModels]],
      ["Global", ["GFS", "ECMWF 9km", "ICON"]],
      ["Regional", ["RAP", "RDPS", "HRDPS"]],
      ["Ensemble", ["REFS Mean", "REFSm1"]],
    ] as const) {
      await forecast.getByRole("radio", { name: region, exact: true }).check();
      for (const model of supported) {
        await expect(forecast.getByRole("button", { name: `Choose ${model}`, exact: true })).toBeVisible();
      }
    }
    const forecastSearch = forecast.getByRole("searchbox", { name: "Search models" });
    await forecastSearch.fill("NAM");
    const forecastResults = forecast.getByRole("group", { name: "CONUS", exact: true });
    await expect(forecastResults.getByRole("button")).toHaveCount(2);
    for (const model of archiveModels) {
      const choice = forecastResults.getByRole("button", { name: `Choose ${model}`, exact: true });
      await expect(choice.getByText("Archive", { exact: true })).toBeVisible();
      await expect(choice).toHaveAccessibleDescription(archiveNotice);
    }
    await expect(forecast.getByText(archiveNotice)).toBeVisible();
    for (const family of retiredFamilies) {
      await forecastSearch.fill(family);
      await expect(forecast.getByRole("button", { name: /^Choose /u })).toHaveCount(0);
      await expect(forecast.getByText("No models found.")).toBeVisible();
    }
    await forecastSearch.fill("");
    await forecast.getByRole("radio", { name: "CONUS", exact: true }).check();
    await expect(forecast.getByRole("button", { name: /^Choose (?:HRW|HiResW)/iu })).toHaveCount(0);
    await expect(forecast.getByText("Archive", { exact: true })).toHaveCount(2);
    await expect(forecast.getByText(archiveNotice)).toBeVisible();
    await saveScreenshot(page, `active-forecast-models-${theme}-1920x1080.png`);
    await forecast.getByRole("button", { name: "Close forecast setup" }).click();

    await workspaces.getByRole("button", { name: "Soundings", exact: true }).click();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    const form = page.getByRole("form", { name: "Model sounding" });
    const soundingModel = form.getByRole("combobox", { name: "Model", exact: true });
    await expect(form.getByText(archiveNotice)).toHaveCount(0);
    await soundingModel.click();
    const sounding = page.getByRole("dialog", { name: "Model", exact: true });
    for (const model of ["HRRR", "RRFS", "ECMWF 9km", "RAP", "ICON"]) {
      await expect(sounding.getByRole("option", { name: model, exact: true })).toBeVisible();
    }
    await expect(sounding.getByRole("option", { name: /^(?:HRW|HiResW)/iu })).toHaveCount(0);
    await expect(sounding.getByRole("group", { name: "Regional", exact: true }).getByRole("option", { name: "RAP", exact: true })).toBeVisible();
    const soundingConus = sounding.getByRole("group", { name: "CONUS", exact: true });
    for (const model of archiveModels) {
      const option = soundingConus.getByRole("option", { name: model, exact: true });
      await expect(option).toContainText("Archive");
      await expect(option).toHaveAccessibleDescription(/^Archive · CONUS/u);
    }
    await expect(sounding.getByText(/^Archive · /u)).toHaveCount(2);
    const soundingSearch = sounding.getByRole("searchbox", { name: "Search models" });
    for (const family of retiredFamilies) {
      await soundingSearch.fill(family);
      await expect(sounding.getByRole("option")).toHaveCount(0);
      await expect(sounding.getByText("No matches.")).toBeVisible();
    }
    await soundingSearch.fill("");
    await saveScreenshot(page, `active-sounding-models-${theme}-1920x1080.png`);
    await soundingSearch.fill("NAM");
    await expect(sounding.getByRole("option")).toHaveCount(2);
    const model = theme === "dark" ? archiveModels[0] : archiveModels[1];
    await sounding.getByRole("option", { name: model, exact: true }).click();
    await expect(soundingModel).toHaveText(model);
    await expect(form.getByText(archiveNotice)).toBeVisible();
    await soundingModel.click();
    await sounding.getByRole("option", { name: "HRRR", exact: true }).click();
    await expect(form.getByText(archiveNotice)).toHaveCount(0);
  }
  expectWarnings(consoleMessages, []);
});

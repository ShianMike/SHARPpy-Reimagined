import { waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";

test("retired models stay out of both selectors in dark and light themes", async ({ page, consoleMessages }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/");
  const workspaces = page.getByRole("navigation", { name: "Workspaces" });
  for (const theme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await workspaces.getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMapDrawn(page);
    await page.getByRole("button", { name: "Choose model", exact: true }).click();
    const forecast = page.getByRole("dialog", { name: "Forecast model" });
    for (const [region, count] of [["CONUS", 7], ["Global", 5], ["Regional", 2]] as const) {
      await forecast.getByRole("radio", { name: region, exact: true }).check();
      await expect(forecast.getByRole("button", { name: /^Choose /u })).toHaveCount(count);
    }
    const forecastSearch = forecast.getByRole("searchbox", { name: "Search models" });
    for (const family of ["NAM", "SREF", "HREF", "HRW", "HiresW"]) {
      await forecastSearch.fill(family);
      await expect(forecast.getByRole("button", { name: /^Choose /u })).toHaveCount(0);
      await expect(forecast.getByText("No models found.")).toBeVisible();
    }
    await forecastSearch.fill("");
    await forecast.getByRole("radio", { name: "CONUS", exact: true }).check();
    await expect(forecast.getByRole("button", { name: "Choose RRFS", exact: true })).toBeVisible();
    await saveScreenshot(page, `active-forecast-models-${theme}-1920x1080.png`);
    await forecast.getByRole("button", { name: "Close forecast setup" }).click();

    await workspaces.getByRole("button", { name: "Soundings", exact: true }).click();
    await page.getByRole("tab", { name: "Model", exact: true }).click();
    await page.getByRole("combobox", { name: "Model", exact: true }).click();
    const sounding = page.getByRole("dialog", { name: "Model", exact: true });
    await expect(sounding.getByRole("option")).toHaveCount(13);
    await expect(sounding.getByRole("option", { name: "RRFS", exact: true })).toBeVisible();
    const soundingSearch = sounding.getByRole("searchbox", { name: "Search models" });
    for (const family of ["NAM", "SREF", "HREF", "HRW", "HiresW"]) {
      await soundingSearch.fill(family);
      await expect(sounding.getByRole("option")).toHaveCount(0);
      await expect(sounding.getByText("No matches.")).toBeVisible();
    }
    await soundingSearch.fill("");
    await saveScreenshot(page, `active-sounding-models-${theme}-1920x1080.png`);
    await page.keyboard.press("Escape");
  }
  expectWarnings(consoleMessages, []);
});

// Backend and map failure states through the fixture scenarios
// (DESIGN-tooling T8, DESIGN-contracts C6): each shows what failed, what was
// involved, and what the user can do, logs its expected console.warn entries,
// and never logs console.error.
import { MARKS, nextFrames, projectStation, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { chooseThroughSearch } from "./support/ui";

test("python-unavailable: the instruction, no Retry, and no station count (DR6-3)", async ({ page, consoleMessages }) => {
  await page.goto("/?scenario=python-unavailable");
  await waitForMark(page, MARKS.mapLoaded);
  await waitForMark(page, MARKS.basemapLoaded);
  const notice = page.getByTestId("catalogue-error");
  await expect(notice).toContainText("Stations could not be loaded");
  await expect(notice).toContainText("Set SHARPPY_PYTHON");
  await expect(notice).toContainText("Python backend, bundled UWyo station catalogue");
  await expect(notice.getByRole("button")).toHaveCount(0);
  await expect(page.getByTestId("catalogue-announcement")).toHaveCount(0);
  const search = page.getByRole("combobox", { name: "Search stations" });
  await page.getByRole("button", { name: "Search stations" }).click();
  await expect(search).toBeDisabled();
  await expect(search).toHaveAttribute("placeholder", "Stations unavailable");
  await page.keyboard.press("Escape");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Logs" }).click();
  await expect(page.getByTestId("health-indicator")).toHaveText("Backend unavailable");
  await page.getByTestId("health-indicator").click();
  const popover = page.getByRole("dialog", { name: "Backend status" });
  await expect(popover).toContainText("SHARPPY_PYTHON");
  await expect(popover).toContainText("sharppy-reimagined.log");
  await page.keyboard.press("Escape");
  await expect(popover).toHaveCount(0);
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer" }).click();
  await nextFrames(page);
  await saveScreenshot(page, "browser-python-unavailable.png");
  expectWarnings(consoleMessages, [{ pattern: /^\[backend\] PYTHON_NOT_FOUND in get_station_catalog/u, count: 1 }]);
});

test("catalog-error: the notice after two automatic retries, then Retry loads 933 stations", async ({
  page,
  consoleMessages,
}) => {
  await page.goto("/?scenario=catalog-error");
  const notice = page.getByTestId("catalogue-error");
  await expect(notice).toContainText("Stations could not be loaded", { timeout: 10_000 });
  await expect(notice).toContainText("The station catalogue could not be loaded.");
  await expect(page.getByTestId("catalogue-announcement")).toHaveCount(0);
  await notice.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByTestId("catalogue-announcement")).toHaveText("933 stations loaded");
  await expect(notice).toHaveCount(0);
  await waitForMark(page, MARKS.stationsRendered);
  expectWarnings(consoleMessages, [{ pattern: /^\[backend\] CATALOG_UNAVAILABLE in get_station_catalog/u, count: 3 }]);
});

test("seed-fallback: the warning, 10 stations, and the seed list in the inspector", async ({ page, consoleMessages }) => {
  await page.goto("/?scenario=seed-fallback");
  await expect(page.getByTestId("catalogue-seed-fallback")).toContainText(
    "Showing the 10-station seed list: the bundled catalogue could not be read",
  );
  await expect(page.getByTestId("catalogue-announcement")).toHaveText("10 stations loaded");
  await waitForMark(page, MARKS.stationsRendered);
  await chooseThroughSearch(page, "72357");
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText("OUN Norman, OK");
  await expect(inspector).toContainText("Seed list, 10 stations");
  expectWarnings(consoleMessages, []);
});

test("restarting: the indicator and its popover say so, and the stations still load", async ({ page, consoleMessages }) => {
  await page.goto("/?scenario=restarting");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Logs" }).click();
  await expect(page.getByTestId("health-indicator")).toHaveText("Restarting backend");
  await page.getByTestId("health-indicator").click();
  await expect(page.getByRole("dialog", { name: "Backend status" })).toContainText(
    "The Python backend stopped unexpectedly and is restarting. Attempt 1.",
  );
  await expect(page.getByTestId("catalogue-announcement")).toHaveText("933 stations loaded");
  expectWarnings(consoleMessages, []);
});

test("slow-catalog: Loading stations until the catalogue arrives", async ({ page, consoleMessages }) => {
  await page.goto("/?scenario=slow-catalog");
  const loading = page.getByTestId("catalogue-loading");
  await expect(loading).toHaveText("Loading stations");
  await expect(page.getByTestId("catalogue-announcement")).toHaveCount(0);
  await page.getByRole("button", { name: "Search stations" }).click();
  await expect(page.getByRole("combobox", { name: "Search stations" })).toHaveAttribute("placeholder", "Loading stations");
  await saveScreenshot(page, "browser-loading.png");
  await expect(page.getByTestId("catalogue-announcement")).toHaveText("933 stations loaded");
  await expect(loading).toHaveCount(0);
  expectWarnings(consoleMessages, []);
});

test("health-error: Backend status unknown with the INTERNAL message, and the stations still load", async ({
  page,
  consoleMessages,
}) => {
  await page.goto("/?scenario=health-error");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Logs" }).click();
  await expect(page.getByTestId("health-indicator")).toHaveText("Backend status unknown");
  await page.getByTestId("health-indicator").click();
  await expect(page.getByRole("dialog", { name: "Backend status" })).toContainText(
    "Something went wrong in get_backend_health. Details are in the log.",
  );
  await expect(page.getByTestId("catalogue-announcement")).toHaveText("933 stations loaded");
  await waitForMark(page, MARKS.stationsRendered);
  expectWarnings(consoleMessages, [{ pattern: /^\[backend\] INTERNAL in get_backend_health/u }]);
});

test("basemap-missing: the notice with Reload map, and the stations still draw and hover", async ({
  page,
  consoleMessages,
}) => {
  await page.goto("/?scenario=basemap-missing");
  const notice = page.getByTestId("map-layers-notice");
  await expect(notice).toContainText("Some map layers could not be drawn");
  await expect(notice.getByRole("button", { name: "Reload map" })).toBeVisible();
  await waitForMark(page, MARKS.stationsRendered);
  await nextFrames(page);
  const point = await projectStation(page, "72357");
  expect(point).not.toBeNull();
  if (point !== null) {
    await page.mouse.move(point.x, point.y);
    await expect(page.getByRole("tooltip")).toContainText("Norman/Max Westheimer A, Ok., Usa");
  }
  await nextFrames(page);
  await saveScreenshot(page, "browser-basemap-missing.png");
  expectWarnings(consoleMessages, [{ pattern: /^\[map\] basemap source "\w+" could not be loaded$/u, count: 4 }]);
});

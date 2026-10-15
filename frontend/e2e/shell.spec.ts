// Desktop workspace navigation, linked forecast controls, and floating panel geometry.
import { MARKS, nextFrames, waitForMapDrawn, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { boxOf, chooseThroughSearch, isInside, overlapArea, viewportBox } from "./support/ui";

test("the forecast workspace has linked controls and only the supported destinations", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  const rail = page.getByRole("navigation", { name: "Workspaces" });
  await expect(rail.locator("[data-workspace-link]")).toHaveText(["Overview", "Model Viewer", "Soundings", "Logs", "Settings"]);
  const navigationBox = await boxOf(rail);
  const mapToolsBox = await boxOf(page.getByRole("toolbar", { name: "Map", exact: true }));
  expect(mapToolsBox.x).toBe(navigationBox.x);
  expect(mapToolsBox.y).toBeGreaterThan(navigationBox.y + navigationBox.height);
  const scale = page.getByRole("complementary", { name: "Parameter scale" });
  await expect(scale).toBeVisible();
  expect((await boxOf(scale)).width).toBe(48);
  await expect(scale.getByRole("listitem")).toHaveCount(0);
  await expect(scale.getByRole("slider", { name: "Explore parameter scale" })).toBeVisible();
  const forecast = page.getByRole("group", { name: "Forecast", exact: true });
  for (const name of ["Choose model", "Choose run", "Choose forecast hour", "Choose parameter"]) {
    await expect(forecast.getByRole("button", { name })).toBeEnabled();
  }
  const timeline = page.getByRole("region", { name: "Forecast timeline" });
  await expect(timeline.getByRole("button", { name: "Previous frame" })).toBeDisabled();
  await timeline.getByRole("button", { name: "Next frame" }).click();
  await expect(forecast.getByRole("button", { name: "Choose forecast hour" })).toContainText("F001");
  await expect(timeline.getByRole("slider", { name: "Forecast hour", exact: true })).toHaveValue("1");
  await expect(timeline.getByLabel("Valid time")).toContainText("10-04 17Z");
  await expect(page.getByTestId("catalogue-announcement")).toHaveText("933 stations loaded");
  await expect(page.getByTestId("health-indicator")).toHaveCount(0);
  await expect(page.getByTestId("panel-chip-1")).toHaveCount(0);
  const actions = page.getByRole("group", { name: "Map actions", exact: true });
  await expect(actions.getByRole("button")).toHaveCount(3);
  const brand = page.getByTestId("product-name").locator("..");
  const topRow = await Promise.all([brand, forecast, actions].map(boxOf));
  expect(topRow.every((box) => Math.abs(box.y - (topRow[0]?.y ?? 0)) < 1)).toBe(true);
  expect(topRow.every((box) => box.height === 56)).toBe(true);
  await expect(forecast).not.toContainText("Latest suggested");
  await expect(forecast).not.toContainText("km ·");
  const selectedHour = forecast.getByRole("button", { name: "Choose forecast hour" });
  const current = await boxOf(selectedHour.locator("strong"));
  const limit = await boxOf(selectedHour.locator("small"));
  expect(limit.x).toBeGreaterThan(current.x + current.width);
  expect(limit.y).toBeLessThan(current.y + current.height);
  const credits = await boxOf(page.locator(".maplibregl-ctrl-bottom-right"));
  expect(credits.x + credits.width).toBe(viewportBox(page).width - 8);
  expect(credits.y + credits.height).toBe(viewportBox(page).height - 8);
  await saveScreenshot(page, "browser-shell.png");
  expectWarnings(consoleMessages, []);
});

test("the compact dock provides one keyboard stop and arrow navigation", async ({ page }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  const rail = page.getByRole("navigation", { name: "Workspaces" });
  const current = rail.getByRole("button", { name: "Model Viewer" });
  await current.focus();
  await page.keyboard.press("ArrowDown");
  await expect(rail.getByRole("button", { name: "Soundings" })).toBeFocused();
  await expect(current).toHaveAttribute("aria-current", "page");
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await expect(rail.getByRole("button", { name: "Settings" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("settings-view")).toBeVisible();
  await expect(rail.locator('button[tabindex="0"]')).toHaveCount(1);
  expect((await boxOf(rail)).width).toBe(56);
});

test("other workspaces leave the map inert and returning preserves the selected station", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  await chooseThroughSearch(page, "72357");
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText("72357");
  const rail = page.getByRole("navigation", { name: "Workspaces" });
  for (const name of ["Overview", "Soundings", "Logs", "Settings"]) {
    await rail.getByRole("button", { name, exact: true }).click();
    await expect(page.getByTestId("workspace-panel")).toBeVisible();
    await expect(page.getByTestId("toolbar")).toHaveCount(0);
    await expect(page.getByRole("group", { name: "Map actions", exact: true })).toHaveCount(0);
    await expect(page.locator('section[aria-label="Model map"]')).toHaveAttribute("inert", "");
    await expect(inspector).toBeHidden();
  }
  await page.getByRole("button", { name: "Device diagnostics", exact: true }).click();
  await expect(page.getByTestId("capability-table")).toContainText("WebGL2");
  await saveScreenshot(page, "browser-settings.png");
  await rail.getByRole("button", { name: "Model Viewer" }).click();
  await expect(inspector).toContainText("72357");
  await page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true }).click();
  await expect(page.getByTestId("layer-panel")).toBeVisible();
  await expect(inspector).toBeHidden();
  await page.getByRole("button", { name: "Close map details" }).press("Escape");
  await expect(page.getByRole("complementary", { name: "Map details" })).toBeHidden();
  await expect(page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Point details", exact: true }).click();
  await expect(inspector).toContainText("72357");
  expectWarnings(consoleMessages, []);
});

test.describe("at the 1024 by 640 minimum window", () => {
  test.use({ viewport: { width: 1024, height: 640 } });

  test("floating controls fit over the full-window map", async ({ page }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    await chooseThroughSearch(page, "73111");
    const surfaces = [
      page.getByTestId("product-name").locator(".."),
      page.getByRole("group", { name: "Map actions", exact: true }),
      page.getByRole("group", { name: "Forecast", exact: true }),
      page.getByRole("complementary", { name: "Map details" }),
      page.getByRole("region", { name: "Forecast timeline" }),
      page.getByRole("complementary", { name: "Parameter scale" }),
      page.getByRole("navigation", { name: "Workspaces" }),
      page.getByRole("toolbar", { name: "Map", exact: true }),
    ];
    const boxes = await Promise.all(surfaces.map(boxOf));
    for (const box of boxes) expect(isInside(box, viewportBox(page))).toBe(true);
    for (const [i, a] of boxes.entries()) {
      for (const b of boxes.slice(i + 1)) expect(overlapArea(a, b)).toBe(0);
    }
    expect(await boxOf(page.getByRole("region", { name: "Model map" }))).toEqual(viewportBox(page));
    await nextFrames(page);
    await saveScreenshot(page, "browser-shell-min.png");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Soundings", exact: true }).click();
    await expect(page.getByTestId("sounding-workspace")).toBeVisible();
  });

  test("search results appear over a map provider notice", async ({ page, consoleMessages }) => {
    await page.goto("/?scenario=basemap-missing");
    await waitForMark(page, MARKS.stationsRendered);
    await expect(page.getByTestId("map-layers-notice")).toBeVisible();
    await page.keyboard.press("Control+k");
    await page.getByRole("combobox", { name: "Search stations" }).fill("norman");
    const option = page.getByRole("listbox", { name: "Station results" }).getByRole("option").first();
    await expect(option).toBeVisible();
    const box = await boxOf(option);
    const onTop = await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('[role="listbox"]')?.getAttribute("aria-label"),
      { x: box.x + box.width / 2, y: box.y + box.height / 2 });
    expect(onTop).toBe("Station results");
    expectWarnings(consoleMessages, [{ pattern: /^\[map\] basemap source "\w+" could not be loaded$/u, count: 4 }]);
  });
});

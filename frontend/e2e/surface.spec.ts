import { camera, deckLayers, nextFrames, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { chooseThroughSearch, hoverAt, layerSettings, turnOn } from "./support/ui";
import { expect, test } from "./support/test";

test("Model Viewer keeps the latest surface stations on the map during zoom", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  await turnOn(page, "surface-plots");
  const settings = await layerSettings(page, "surface-plots");
  await expect(settings.getByTestId("surface-shown")).toContainText("Latest METAR");
  await expect(settings.getByTestId("surface-shown")).toHaveText(/Reporting stations\s*4/u);
  const layers = page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true });
  await layers.click();
  await expect.poll(async () => (await deckLayers(page)).some((id) => id.startsWith("surface-plots"))).toBe(true);

  await chooseThroughSearch(page, "72357");
  await page.getByRole("button", { name: "Zoom to station", exact: true }).click();
  await expect.poll(async () => (await camera(page))?.zoom ?? 0).toBeGreaterThanOrEqual(5);
  for (const action of ["Zoom out", "Zoom in"]) {
    const zoom = (await camera(page))?.zoom;
    await page.getByRole("button", { name: action, exact: true }).click();
    await expect.poll(async () => (await camera(page))?.zoom).not.toBe(zoom);
    await expect.poll(async () => (await deckLayers(page)).some((id) => id.startsWith("surface-plots"))).toBe(true);
  }
  await hoverAt(page, -98.5, 33.9);
  await expect(page.getByTestId("report-tooltip")).toContainText("KSPS");
  await saveScreenshot(page, "browser-forecast-surface.png");
  expectWarnings(consoleMessages, []);
});

test("Live Tracking shares the latest surface stations and reveals plots when zoomed in", async ({ page, consoleMessages }) => {
  await page.clock.setFixedTime(new Date("2026-10-04T21:00:00Z"));
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Live Tracking", exact: true }).click();
  await waitForMapDrawn(page);
  await turnOn(page, "surface-plots");
  const settings = await layerSettings(page, "surface-plots");
  await expect(settings.getByTestId("surface-shown")).toContainText("Latest METAR");
  await expect(settings.getByTestId("surface-shown")).toHaveText(/Reporting stations\s*4/u);
  const layers = page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true });
  await layers.click();
  await expect.poll(() => deckLayers(page)).toContain("surface-plots-markers");
  await expect.poll(() => deckLayers(page)).not.toContain("surface-plots");

  await chooseThroughSearch(page, "72357");
  // At CONUS scale station models are decluttered to markers; inspect them after zooming in.
  await page.getByRole("button", { name: "Zoom to station", exact: true }).click();
  await expect.poll(async () => (await camera(page))?.zoom ?? 0).toBeGreaterThanOrEqual(5);
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expect.poll(async () => (await camera(page))?.zoom ?? 0).toBeGreaterThanOrEqual(6);
  await expect.poll(() => deckLayers(page)).toContain("surface-plots");
  await nextFrames(page);
  await hoverAt(page, -98.5, 33.9);
  await expect(page.getByTestId("report-tooltip")).toContainText("KSPS");
  await nextFrames(page);
  await saveScreenshot(page, "browser-live-surface.png");
  expectWarnings(consoleMessages, []);
});

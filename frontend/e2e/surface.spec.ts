// Surface observations on the forecast map (REQUEST surface observations):
// the station models around the map's own selection, their every state, and
// the hover readout. The committed fixture set answers regardless of the
// anchor, so the assertions are the readouts and the states, not fetch
// geography.

import { camera, deckLayers, nextFrames, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { chooseThroughSearch, hoverAt, layerRow, turnOn } from "./support/ui";
import { expect, test } from "./support/test";

test("surface observations: the station models around the selection", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  await turnOn(page, "surface-plots");
  const row = await layerRow(page, "surface-plots");
  // Live observations start around the map centre before a location is selected.
  await expect(row.getByTestId("surface-shown")).toContainText("4 observations");
  await chooseThroughSearch(page, "72357");
  // At CONUS scale nearby station models are decluttered; inspect them after zooming in.
  await page.getByRole("button", { name: "Zoom to station", exact: true }).click();
  await expect.poll(async () => (await camera(page))?.zoom ?? 0).toBeGreaterThanOrEqual(5);
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expect.poll(async () => (await camera(page))?.zoom ?? 0).toBeGreaterThanOrEqual(6);
  // Every plot state counts: matched, stale, checked without a match, failed.
  await layerRow(page, "surface-plots");
  const status = row.getByTestId("surface-shown");
  await expect(status).toContainText("4 observations · 6 of 6 stations checked");
  await expect(status).toContainText("1 stale observations · shown with a dashed ring");
  await expect(status).toContainText("1 without a report · 1 could not be checked");
  await expect.poll(() => deckLayers(page)).toContain("surface-plots");
  await nextFrames(page);
  // A checked station's ring reads as its own hover block.
  await hoverAt(page, -97.0, 34.0);
  await expect(page.getByTestId("report-tooltip")).toContainText("KFOO");
  await nextFrames(page);
  await saveScreenshot(page, "browser-forecast-surface.png");
  expectWarnings(consoleMessages, []);
});

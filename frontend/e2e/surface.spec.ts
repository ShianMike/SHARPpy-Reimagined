// Surface observations on the forecast map (REQUEST surface observations):
// the station models around the map's own selection, their every state, and
// the hover readout. The committed fixture set answers regardless of the
// anchor, so the assertions are the readouts and the states, not fetch
// geography.

import { camera, deckLayers, nextFrames, projectPoint, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { hoverAt, layerRow, turnOn } from "./support/ui";
import { expect, test } from "./support/test";

test("surface observations: the station models around the selection", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  await turnOn(page, "surface-plots");
  const row = await layerRow(page, "surface-plots");
  // Without a selection the layer says so instead of guessing a place.
  await expect(row).toContainText("Select a station or a point");
  const point = await projectPoint(page, -97.4, 35.2);
  expect(point).not.toBeNull();
  if (point !== null) {
    await page.mouse.click(point.x, point.y);
  }
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

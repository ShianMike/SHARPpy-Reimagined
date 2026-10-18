// MapLibre draws the offline basemap (DESIGN-tooling T8, the risk 2 gate):
// the bundled worker loads the four GeoJSON sources under the app's CSP, the
// line layers render features, and the map region is not a uniform colour with
// the deck.gl layers switched off.
import { basemapFeatureCounts, MARKS, mapErrors, nextFrames, waitForMark } from "./support/app";
import { regionStats } from "./support/pixels";
import { saveScreenshot, screenshotPath } from "./support/screenshots";
import { expect, test } from "./support/test";
import { layerRow } from "./support/ui";

/** Inside the map overlay, clear of the map toolbar and the layer panel, at 1440 by 900. */
const MAP_REGION = { x: 160, y: 80, width: 900, height: 600 };

test("MapLibre draws the offline basemap", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMark(page, MARKS.mapLoaded);
  await waitForMark(page, MARKS.basemapLoaded);

  const counts = await basemapFeatureCounts(page);
  expect(counts["basemap-coastline"]).toBeGreaterThan(0);
  expect(counts["basemap-states"]).toBeGreaterThan(0);
  expect(await mapErrors(page)).toEqual([]);
  await expect(page.getByText("Some map layers could not be drawn")).toHaveCount(0);
  await expect(page.locator(".maplibregl-ctrl-attrib")).toContainText("Natural Earth");

  await waitForMark(page, MARKS.stationsRendered);
  await nextFrames(page);
  await saveScreenshot(page, "browser-map.png");

  // The basemap alone: the station and label layers off.
  const stations = (await layerRow(page, "stations")).getByRole("switch", { name: "Stations", exact: true });
  const labels = (await layerRow(page, "station-labels")).getByRole("switch", { name: "Station labels", exact: true });
  await stations.click();
  await labels.setChecked(false);
  await expect(stations).toHaveAttribute("aria-checked", "false");
  await expect(labels).toHaveAttribute("aria-checked", "false");
  await nextFrames(page);
  await page.screenshot({ path: screenshotPath("browser-basemap-only.png") });
  const region = await page.screenshot({ clip: MAP_REGION });
  const stats = await regionStats(page, region);
  test.info().annotations.push({ type: "basemap-region", description: JSON.stringify(stats) });
  expect(stats.distinct).toBeGreaterThan(8);
  expect(stats.stdDev).toBeGreaterThan(2);
});

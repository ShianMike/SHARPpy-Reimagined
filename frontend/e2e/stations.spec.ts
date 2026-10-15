// deck.gl draws and picks the stations (DESIGN-tooling T8): the
// stations-rendered mark, the marker colour at station 72357's projected
// position, and its tooltip on hover; a click selects it in the toolbar chip
// and the inspector, and Esc clears it; labels appear by tier as the view
// narrows.
import { effectiveProfile, labels, MARKS, nextFrames, presetPreferences, projectStation, waitForMapDrawn, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { pixelAt } from "./support/pixels";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";

/** --map-station-unknown, #9c978d. */
const UNKNOWN_MARKER: [number, number, number] = [156, 151, 141];

test("deck.gl draws station 72357 and shows its tooltip on hover", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMark(page, MARKS.stationsRendered);
  await nextFrames(page);

  const profile = await effectiveProfile(page);
  test.info().annotations.push({ type: "effective-profile", description: JSON.stringify(profile) });

  const point = await projectStation(page, "72357");
  expect(point).not.toBeNull();
  if (point === null) {
    return;
  }
  // The marker is drawn: the pixel under the station has its availability colour.
  const x = Math.round(point.x);
  const y = Math.round(point.y);
  const shot = await page.screenshot({ clip: { x: x - 2, y: y - 2, width: 5, height: 5 } });
  const [r, g, b] = await pixelAt(page, shot, 2, 2);
  test.info().annotations.push({ type: "marker-pixel", description: JSON.stringify([r, g, b]) });
  for (const [actual, expected] of [
    [r, UNKNOWN_MARKER[0]],
    [g, UNKNOWN_MARKER[1]],
    [b, UNKNOWN_MARKER[2]],
  ] as const) {
    expect(Math.abs(actual - expected)).toBeLessThanOrEqual(24);
  }

  // Picking: hovering the projected position shows the tooltip.
  await page.mouse.move(point.x, point.y);
  const tooltip = page.getByRole("tooltip");
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toContainText("72357");
  await expect(tooltip).toContainText("Norman/Max Westheimer A, Ok., Usa");
  await expect(tooltip).toContainText("35.18° N, 97.44° W");
  await expect(tooltip).toContainText("Availability unknown");
  await expect(tooltip).toContainText("UWyo FM35");
  await nextFrames(page);
  await saveScreenshot(page, "browser-hover.png");
  expectWarnings(consoleMessages, []);
});

test("a click selects 72357 in the chip and the inspector, and Esc clears it", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  const point = await projectStation(page, "72357");
  expect(point).not.toBeNull();
  if (point === null) {
    return;
  }
  await page.mouse.click(point.x, point.y);
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText("Norman/Max Westheimer A, Ok., Usa");
  await expect(inspector).toContainText("35.18° N");
  await expect(inspector).toContainText("97.44° W");
  await expect(inspector).toContainText("Unknown");
  await expect(inspector).toContainText("UWyo FM35");
  await expect(inspector).toContainText("Bundled UWyo list, 933 stations");
  // Off the map (over the timeline), so no tooltip covers the selection.
  await page.mouse.move(720, 872);
  await nextFrames(page);
  await saveScreenshot(page, "browser-selected.png");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Select a point" })).toBeVisible();
  await expect(inspector).toHaveCount(0);
  expectWarnings(consoleMessages, []);
});

test("labels appear by tier as the view narrows", async ({ page, consoleMessages }) => {
  await presetPreferences(page, { layers: { "station-labels": { visible: true, opacity: 1 } } });
  await page.goto("/");
  await waitForMark(page, MARKS.stationsRendered);
  // The CONUS view spans more than 55 degrees of longitude: no labels (Balanced, F5).
  expect(await labels(page)).toEqual({ count: 0, tier: null });
  const zoomIn = page.getByRole("button", { name: "Zoom in" });
  await zoomIn.click();
  await expect.poll(async () => (await labels(page)).tier).toBe("id");
  expect((await labels(page)).count).toBeGreaterThan(0);
  await zoomIn.click();
  await expect.poll(async () => (await labels(page)).tier).toBe("name");
  expect((await labels(page)).count).toBeGreaterThan(0);
  expectWarnings(consoleMessages, []);
});

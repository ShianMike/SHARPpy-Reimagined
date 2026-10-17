// The reduced-rendering mode (DESIGN-tooling T8, DESIGN-frontend F6): with
// getContext("webgl2") returning null, the notice and the station list appear
// with no Try the map again button, and keyboard selection fills the
// inspector. DR6-2: with Python unavailable as well, the configuration
// instruction sits above the list's empty state.
import { MARKS, nextFrames, stubNoWebgl2, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { boxOf } from "./support/ui";

const NO_WEBGL2 = "Map rendering is off: WebGL2 is not available on this device.";

test("without WebGL2 the station list replaces the map, and the keyboard selects", async ({ page, consoleMessages }) => {
  await stubNoWebgl2(page);
  await page.goto("/");
  await waitForMark(page, MARKS.catalogReceived);
  const notice = page.getByTestId("reduced-notice");
  await expect(notice).toContainText(NO_WEBGL2);
  await expect(notice).toContainText("Station search, selection, and the inspector still work.");
  await expect(page.getByRole("button", { name: "Try the map again" })).toHaveCount(0);
  await expect(page.getByTestId("map")).toHaveCount(0);
  const list = page.getByRole("listbox", { name: "Stations" });
  await expect(list.getByRole("option")).toHaveCount(934);

  await list.focus();
  await page.keyboard.press("End");
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  const second = list.getByRole("option").nth(1);
  await expect(second).toHaveAttribute("aria-selected", "true");
  const id = ((await second.textContent()) ?? "").split(" ")[0] ?? "";
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText(id);
  await expect(inspector).toContainText("Bundled UWyo list, 934 stations");
  // The list stops short of the inspector, so the two never overlap.
  const listBox = await boxOf(list);
  const inspectorBox = await boxOf(inspector);
  expect(listBox.x + listBox.width).toBeLessThanOrEqual(inspectorBox.x);
  await nextFrames(page);
  await saveScreenshot(page, "browser-fallback.png");
  expectWarnings(consoleMessages, []);
});

test("DR6-2: without WebGL2 or Python, the instruction sits above the empty list", async ({ page, consoleMessages }) => {
  await stubNoWebgl2(page);
  await page.goto("/?scenario=python-unavailable");
  await waitForMark(page, MARKS.shellPainted);
  await expect(page.getByTestId("reduced-notice")).toContainText(NO_WEBGL2);
  const instruction = page.getByTestId("catalogue-error");
  await expect(instruction).toContainText("Stations could not be loaded");
  await expect(instruction).toContainText("Set SHARPPY_PYTHON");
  await expect(instruction.getByRole("button")).toHaveCount(0);
  const empty = page.getByTestId("station-list-empty");
  await expect(empty).toHaveText("No stations to list");
  const instructionBox = await boxOf(instruction);
  const emptyBox = await boxOf(empty);
  expect(instructionBox.y + instructionBox.height).toBeLessThanOrEqual(emptyBox.y);
  await page.getByRole("button", { name: "Search stations" }).click();
  await expect(page.getByRole("combobox", { name: "Search stations" })).toBeDisabled();
  expectWarnings(consoleMessages, [{ pattern: /^\[backend\] PYTHON_NOT_FOUND in get_station_catalog/u, count: 1 }]);
});

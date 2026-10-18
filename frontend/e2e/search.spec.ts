// The toolbar station search (DESIGN-tooling T8): Ctrl+K focuses it,
// "norman" lists the search_stations() matches, ArrowDown and Enter choose
// one, and the camera flies to the station.
import { camera, MARKS, nextFrames, waitForMapDrawn, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";

/** 72357 in the golden catalogue. */
const NORMAN = { longitude: -97.43787, latitude: 35.18095 };

test("Ctrl+K, norman, ArrowDown and Enter select 72357 and the camera moves there", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  const before = await camera(page);
  expect(before).not.toBeNull();

  const input = page.getByRole("combobox", { name: "Search stations" });
  await page.keyboard.press("Control+k");
  await expect(input).toBeFocused();
  await page.keyboard.type("norman");
  const listbox = page.getByRole("listbox", { name: "Station results" });
  await expect(input).toHaveAttribute("aria-expanded", "true");
  await expect(input).toHaveAttribute("aria-controls", (await listbox.getAttribute("id")) ?? "");
  const options = listbox.getByRole("option");
  await expect(options).toHaveCount(3);
  await expect(options.nth(0)).toHaveAccessibleName("71043 Norman Wells Ua, Nwt, Canada");
  await expect(options.nth(1)).toHaveAccessibleName("72357 Norman/Max Westheimer A, Ok., Usa");
  await expect(options.nth(2)).toHaveAccessibleName("78397 Kingston/Norman Manley, Jamaica");

  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
  await expect(input).toHaveAttribute("aria-activedescendant", (await options.nth(1).getAttribute("id")) ?? "");
  await nextFrames(page);
  await saveScreenshot(page, "browser-search.png");

  await page.keyboard.press("Enter");
  await expect(listbox).toHaveCount(0);
  await expect(page.getByRole("complementary", { name: "Inspector" })).toContainText("72357");
  await expect
    .poll(async () => {
      const now = await camera(page);
      return now === null
        ? Number.POSITIVE_INFINITY
        : Math.max(Math.abs(now.center[0] - NORMAN.longitude), Math.abs(now.center[1] - NORMAN.latitude));
    })
    .toBeLessThan(0.01);
  const after = await camera(page);
  expect(after?.zoom).toBeGreaterThanOrEqual(5);
  expect(after?.zoom).toBeGreaterThan(before?.zoom ?? Number.POSITIVE_INFINITY);
  expectWarnings(consoleMessages, []);
});

test("/ outside a text field focuses the search, and an unmatched query says so", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMark(page, MARKS.catalogReceived);
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer" }).focus();
  await page.keyboard.press("/");
  const input = page.getByRole("combobox", { name: "Search stations" });
  await expect(input).toBeFocused();
  await expect(input).toHaveValue("");
  await page.keyboard.type("zzqx");
  await expect(page.getByRole("listbox", { name: "Station results" })).toHaveText('No stations match "zzqx"');
  await page.keyboard.press("Escape");
  await expect(page.getByRole("listbox", { name: "Station results" })).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "Station search" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Search stations" })).toBeFocused();
  await page.keyboard.press("Control+k");
  await expect(input).toHaveValue("");
  expectWarnings(consoleMessages, []);
});

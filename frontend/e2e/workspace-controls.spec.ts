import { nextFrames, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { boxOf, isInside, viewportBox } from "./support/ui";

test.use({ viewport: { width: 1280, height: 800 } });

test("empty log results are centered within the log body", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Logs", exact: true }).click();
  await page.getByRole("searchbox", { name: "Search logs" }).fill("no-such-session-event");
  const empty = page.getByRole("heading", { name: "No matching events" }).locator("..");
  await expect(empty).toBeVisible();
  const [content, body] = await Promise.all([boxOf(empty), boxOf(empty.locator(".."))]);
  expect(Math.abs(content.x + content.width / 2 - body.x - body.width / 2)).toBeLessThan(1);
  expect(Math.abs(content.y + content.height / 2 - body.y - body.height / 2)).toBeLessThan(1);
  await saveScreenshot(page, "browser-logs-centered.png");
  await empty.getByRole("button", { name: "Reset filters" }).click();
  await expect(page.getByRole("table", { name: "Session events" })).toBeVisible();
  expectWarnings(consoleMessages, []);
});

test("Settings uses plain headings and a plain automatic-save hint", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Settings", exact: true }).click();
  const navigation = page.getByRole("navigation", { name: "Settings sections" });
  for (const name of ["Appearance", "Units", "Forecast playback", "Map rendering", "Device diagnostics", "Updates"]) {
    await navigation.getByRole("button", { name, exact: true }).click();
    const heading = page.getByRole("heading", { name, exact: true, level: 2 }).locator("..");
    await expect(heading).toBeVisible();
    await expect(heading.locator("svg")).toHaveCount(0);
  }
  await expect(navigation.locator("svg")).toHaveCount(6);
  const saved = page.getByText("Changes save automatically on this device.", { exact: true });
  await expect(saved).toBeVisible();
  await expect(saved.locator("svg")).toHaveCount(0);
  await expect(page.getByText("Current version: 3.0.2", { exact: true })).toBeVisible();
  await saveScreenshot(page, "browser-settings-plain.png");
  expectWarnings(consoleMessages, []);
});

test("unit choices fit at 1280 × 800, survive reload and reach the parameter picker and scale", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  const settings = page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Settings", exact: true });
  await settings.click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Units", exact: true }).click();
  const content = page.locator("#settings-content");
  await expect(content.getByRole("radio")).toHaveCount(7);
  await content.getByText("Celsius", { exact: true }).click();
  await content.getByText("Kilometres/h", { exact: true }).click();
  await expect(content.getByRole("radio", { name: "°C Celsius" })).toBeChecked();
  await expect(content.getByText("37 km/h", { exact: true })).toBeVisible();
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await nextFrames(page);
    expect(await content.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
    expect(await content.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await expect(content.getByRole("button", { name: "Reset units" })).toBeVisible();
    await saveScreenshot(page, `browser-settings-units-${colorScheme}.png`);
  }
  await page.reload();
  await waitForMapDrawn(page);
  await settings.click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Units", exact: true }).click();
  await expect(content.getByRole("radio", { name: "°C Celsius" })).toBeChecked();
  await expect(content.getByRole("radio", { name: "km/h Kilometres/h" })).toBeChecked();
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await page.getByRole("button", { name: "Choose parameter", exact: true }).click();
  const picker = page.getByRole("dialog", { name: "Parameters", exact: true });
  await picker.getByRole("searchbox", { name: "Search parameters" }).fill("Temperature");
  await picker.getByRole("button", { name: "2 m AGL Temperature, °C", exact: true }).click();
  const scale = page.getByRole("complementary", { name: "Parameter scale" });
  await expect(scale).toHaveAttribute("aria-description", /°C/u);
  await scale.getByRole("slider").focus();
  await expect(scale.getByRole("slider")).toHaveAttribute("aria-valuetext", /°C/u);
  expectWarnings(consoleMessages, []);
});

test("panel titles open their parameter picker and selected categories stay on one line", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  await page.getByRole("toolbar", { name: "Map", exact: true }).getByRole("button", { name: "Four synchronized panels" }).click();
  const first = page.getByTestId("panel-chip-1");
  const firstTitle = await first.locator("span").first().textContent();
  const title = page.getByTestId("panel-chip-4");
  await title.click();
  const picker = page.getByRole("dialog", { name: "Parameters", exact: true });
  await expect(picker).toBeVisible();
  await expect(picker.getByRole("searchbox", { name: "Search parameters" })).toBeFocused();
  const categories = picker.getByRole("navigation", { name: "Parameter categories" });
  const category = categories.getByRole("button", { name: /^Composite Parameters/u });
  const before = await boxOf(category);
  const sidebarWidth = (await boxOf(categories)).width;
  await category.click();
  await nextFrames(page);
  expect((await boxOf(category)).height).toBe(before.height);
  expect(await category.locator("span").evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return range.getClientRects().length;
  })).toBe(1);
  expect(await category.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect((await boxOf(categories)).width).toBe(sidebarWidth);
  const [label, count] = await Promise.all([boxOf(category.locator("span")), boxOf(category.locator("small"))]);
  expect(count.x - label.x - label.width).toBeGreaterThanOrEqual(12);
  await picker.getByRole("button", { name: "Significant Tornado Parameter, Index", exact: true }).focus();
  const detail = picker.locator("#parameter-description");
  await expect(detail).toContainText("Significant Tornado Parameter");
  await expect(detail).toContainText("Fixed-layer form");
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await nextFrames(page);
    expect(await categories.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
    expect(await detail.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
    expect(isInside(await boxOf(picker), viewportBox(page))).toBe(true);
    await saveScreenshot(page, `browser-panel-parameters-${colorScheme}.png`);
  }
  await picker.getByRole("searchbox", { name: "Search parameters" }).fill("Temperature");
  await picker.getByRole("navigation", { name: "Parameter categories" }).getByRole("button", { name: /^All/u }).click();
  await picker.getByRole("button", { name: "2 m AGL Temperature, °F", exact: true }).click();
  await expect(picker).toBeHidden();
  await expect(title).toContainText("2 m AGL Temperature");
  await expect(first.locator("span").first()).toHaveText(firstTitle ?? "");
  await expect(title).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(picker).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(picker).toBeHidden();
  await expect(title).toBeFocused();
  expectWarnings(consoleMessages, []);
});

import appManifest from "../package.json" with { type: "json" };
import { nextFrames, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { boxOf, isInside, viewportBox } from "./support/ui";

test.use({ viewport: { width: 1280, height: 800 } });

test("empty log results are centered within the log body", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
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
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Settings", exact: true }).click();
  const navigation = page.getByRole("navigation", { name: "Settings sections" });
  const sections = ["Workspace", "Appearance", "Units", "Playback", "Map rendering", "Storage", "Device Info", "Updates"];
  for (const name of sections) {
    await navigation.getByRole("button", { name, exact: true }).click();
    const heading = page.getByRole("heading", { name, exact: true, level: 2 }).locator("..");
    await expect(heading).toBeVisible();
    await expect(heading.locator("svg")).toHaveCount(0);
  }
  await expect(navigation.locator("svg")).toHaveCount(sections.length);
  const saved = page.getByText("Changes save automatically on this device.", { exact: true });
  await expect(saved).toBeVisible();
  expect(isInside(await boxOf(saved), await boxOf(saved.locator("..")))).toBe(true);
  await expect(saved.locator("svg")).toHaveCount(0);
  await expect(page.getByText(`Current version: ${appManifest.version}`, { exact: true })).toBeVisible();
  await saveScreenshot(page, "browser-settings-plain.png");
  expectWarnings(consoleMessages, []);
});

test("unit choices fit at 1280 × 800, survive reload and reach the parameter picker and scale", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
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
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
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

test("panel titles only appear in Model Viewer and return with the saved layout", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  await page.getByRole("toolbar", { name: "Map", exact: true }).getByRole("button", { name: "Four synchronized panels" }).click();
  const rail = page.getByRole("navigation", { name: "Workspaces" });
  const titles = page.getByTestId(/^panel-chip-[1-4]$/u);
  await expect(titles).toHaveCount(4);
  const labels = await titles.locator("span:first-child").allTextContents();
  for (const name of ["Overview", "Logs", "Soundings", "Settings"]) {
    await rail.getByRole("button", { name, exact: true }).click();
    await expect(page.getByTestId("workspace-panel")).toHaveAttribute("aria-label", name);
    await expect(titles).toHaveCount(0);
    await expect(page.getByTestId(/^map-panel-[1-4]$/u)).toHaveCount(4);
  }
  await page.getByTestId("workspace-panel").evaluate(async (element) => { await Promise.allSettled(element.getAnimations().map((animation) => animation.finished)); });
  await saveScreenshot(page, "browser-settings-without-panel-titles.png");
  await rail.getByRole("button", { name: "Model Viewer", exact: true }).click();
  await expect(titles.locator("span:first-child")).toHaveText(labels);
  await titles.last().click();
  await expect(page.getByRole("dialog", { name: "Parameters", exact: true })).toBeVisible();
  expectWarnings(consoleMessages, []);
});

test("panel titles open their parameter picker and selected categories stay on one line", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
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
    expect(await categories.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect((await boxOf(detail)).height).toBeLessThanOrEqual(120);
    await detail.press("End");
    await expect.poll(() => detail.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThanOrEqual(1);
    expect(isInside(await boxOf(picker), viewportBox(page))).toBe(true);
    await saveScreenshot(page, `browser-panel-parameters-${colorScheme}.png`);
  }
  await categories.getByRole("button", { name: /^All/u }).click();
  const results = picker.getByRole("region", { name: "Parameter results" });
  for (const [label, unit] of [
    ["Updraft Helicity: 0-3 km AGL (run max)", "m^2/s^2"],
    ["Significant Tornado Parameter", "Index"],
    ["Effective Storm Relative Helicity", "m^2/s^2"],
    ["Energy Helicity Index: 0-1 km AGL", "Index"],
    ["Energy Helicity Index: 0-3 km AGL", "Index"],
  ] as const) {
    const product = results.getByRole("button", { name: `${label}, ${unit}`, exact: true });
    await product.scrollIntoViewIfNeeded();
    const listBox = await boxOf(results);
    const rowBox = await boxOf(product);
    const scrollTop = await results.evaluate((element) => element.scrollTop);
    await product.hover();
    await expect(detail.locator("strong")).toContainText(label);
    await nextFrames(page);
    expect(await boxOf(results)).toEqual(listBox);
    expect(await boxOf(product)).toEqual(rowBox);
    expect(await results.evaluate((element) => element.scrollTop)).toBe(scrollTop);
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

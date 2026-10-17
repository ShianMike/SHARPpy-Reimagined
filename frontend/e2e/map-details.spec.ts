import { camera, deckLayers, mapErrors, nextFrames, waitForMapDrawn } from "./support/app";
import { regionStats } from "./support/pixels";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { chooseThroughSearch, layerRow } from "./support/ui";

test("map projection survives reload and style changes, and resets with layers", async ({ page }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  const domain = await layerRow(page, "model-domain");
  await domain.getByRole("switch").setChecked(true);
  const projection = page.getByRole("group", { name: "Map projection" });
  await projection.getByRole("button", { name: "Globe", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__SHARPPY_E2E__?.projection())).toBe("globe");
  await expect.poll(() => deckLayers(page)).toContain("model-domain");
  await page.reload();
  await waitForMapDrawn(page);
  await expect.poll(() => page.evaluate(() => window.__SHARPPY_E2E__?.projection())).toBe("globe");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Appearance", exact: true }).click();
  await page.getByRole("radio", { name: /Light/u }).check();
  await expect.poll(() => page.evaluate(() => window.__SHARPPY_E2E__?.projection())).toBe("globe");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await layerRow(page, "model-domain");
  await expect(projection.getByRole("button", { name: "Globe", exact: true })).toHaveAttribute("aria-pressed", "true");
  await saveScreenshot(page, "browser-globe-domain.png");
  const position = await camera(page);
  await page.getByRole("button", { name: "Reset layers", exact: true }).click();
  await page.getByRole("button", { name: "Map details", exact: true }).click();
  await expect(projection.getByRole("button", { name: "Flat map", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => page.evaluate(() => window.__SHARPPY_E2E__?.projection())).toBe("mercator");
  expect(await camera(page)).toEqual(position);
  expect(await mapErrors(page)).toEqual([]);
});

test("theta-e renders its fixed Kelvin palette and wind barbs in both projections", async ({ page }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  await page.getByRole("button", { name: "Choose parameter", exact: true }).click();
  const picker = page.getByRole("dialog", { name: "Parameters", exact: true });
  await picker.getByRole("searchbox", { name: "Search parameters" }).fill("Theta-e");
  await picker.getByRole("button", { name: "2 m AGL Theta-e, 10 m AGL Wind, K", exact: true }).click();
  const field = await layerRow(page, "model-field");
  await field.getByRole("switch").setChecked(true);
  await field.getByRole("slider", { name: "Model field opacity", exact: true }).focus();
  await page.keyboard.press("End");
  await page.getByRole("button", { name: "Map details", exact: true }).click();
  for (const projection of ["Globe", "Flat map"]) {
    await page.getByRole("button", { name: projection, exact: true }).click();
    await expect.poll(() => deckLayers(page)).toContain("model-wind-barbs");
    const scale = page.getByRole("complementary", { name: "Parameter scale" });
    await expect(scale).toHaveAttribute("aria-description", /K/u);
    await scale.getByRole("slider").focus();
    await page.keyboard.press("Home");
    await expect(scale.getByRole("slider")).toHaveAttribute("aria-valuetext", "229 K");
    await page.getByRole("button", { name: "Close map details", exact: true }).click();
    await nextFrames(page);
    const clip = { x: 180, y: 200, width: 600, height: 400 };
    const shown = await regionStats(page, await page.screenshot({ clip }));
    // This area is outside the fixture's coloured field: only wind symbols change.
    const barbClip = { x: 300, y: 180, width: 400, height: 220 };
    const barbsShown = await regionStats(page, await page.screenshot({ clip: barbClip }));
    await saveScreenshot(page, `browser-thetae-${projection === "Globe" ? "globe" : "flat"}.png`);
    await scale.getByRole("button", { name: "Model layer", exact: true }).click();
    await nextFrames(page);
    const hidden = await regionStats(page, await page.screenshot({ clip }));
    expect(Math.abs(shown.mean - hidden.mean)).toBeGreaterThan(2);
    const barbsHidden = await regionStats(page, await page.screenshot({ clip: barbClip }));
    expect(barbsShown.mean - barbsHidden.mean).toBeGreaterThan(0.2);
    await scale.getByRole("button", { name: "Model layer", exact: true }).click();
    await layerRow(page, "model-domain");
  }
  expect(await mapErrors(page)).toEqual([]);
});

test("Point buttons and source disclosure use the accent on hover and keyboard focus", async ({ page }) => {
  await page.goto("/");
  await waitForMapDrawn(page);
  await chooseThroughSearch(page, "72357");
  const point = page.getByRole("complementary", { name: "Map details", exact: true });
  const summary = point.locator("summary");
  const zoom = point.getByRole("button", { name: "Zoom to station", exact: true });
  for (const colorScheme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme });
    const accent = await point.getByRole("heading", { name: "Point", exact: true }).locator("svg").evaluate((element) => getComputedStyle(element).color);
    await summary.hover();
    await expect(summary).toHaveCSS("color", accent);
    await zoom.hover();
    await expect(zoom).toHaveCSS("color", accent);
    await page.mouse.move(0, 0);
    await summary.focus();
    await page.keyboard.press("Tab");
    await expect(zoom).toBeFocused();
    await expect(zoom).toHaveCSS("color", accent);
    await page.keyboard.press("Shift+Tab");
    await expect(summary).toBeFocused();
    await expect(summary).toHaveCSS("color", accent);
    await saveScreenshot(page, `browser-point-focus-${colorScheme}.png`);
  }
});

// Verify the rendered control states, glass profiles and readable identifiers.
import type { Page } from "@playwright/test";
import appManifest from "../package.json" with { type: "json" };
import { effectiveProfile, MARKS, presetPreferences, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { toolbarGlass } from "./support/glass";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { chooseThroughSearch, resolved } from "./support/ui";

test("shared controls and local overrides follow hover tokens in both themes", async ({ page, consoleMessages }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await presetPreferences(page, { graphicsOverride: "low", productKey: "tmp-2m",
    layers: { "model-field": { visible: true, opacity: 0.5, smoothing: false } } });
  await page.addInitScript((version) => {
    localStorage.setItem("sharppy-workspace-ui", JSON.stringify({ state: { lastVersion: version }, version: 0 }));
  }, appManifest.version);
  await page.goto("/");
  const rail = page.getByRole("navigation", { name: "Workspaces" });
  await rail.getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMark(page, MARKS.stationsRendered);
  for (const theme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const fill = await resolved(page, "--accent-subtle", "color");
    const ink = await resolved(page, "--accent-bright", "color");
    expect(await resolved(page, "--fill-hover", "color")).toBe(fill);
    expect(await resolved(page, "--scrollbar-thumb-hover", "color")).toBe(ink);
    const settings = rail.getByRole("button", { name: "Settings", exact: true });
    const zoom = page.getByRole("button", { name: "Zoom in", exact: true });
    for (const control of [settings, zoom]) {
      await control.hover();
      await expect(control).toHaveCSS("background-color", fill);
      await expect(control).toHaveCSS("color", ink);
    }
    const disabled = page.getByRole("button", { name: "Previous frame", exact: true });
    await disabled.hover();
    await expect(disabled).toBeDisabled();
    await expect(disabled).toHaveCSS("color", await resolved(page, "--text-disabled", "color"));
    await page.getByRole("button", { name: "Choose forecast hour", exact: true }).click();
    const downloads = page.getByRole("dialog", { name: "Forecast hour", exact: true });
    const load = downloads.getByRole("button", { name: "Load Hours", exact: true });
    await expect(load).toBeEnabled();
    await load.hover();
    await expect(load).toHaveCSS("background-color", await resolved(page, "--accent-hover", "color"));
    await expect(load).toHaveCSS("color", await resolved(page, "--accent-text", "color"));
    await downloads.press("Escape");
    await settings.click();
    const sections = page.getByRole("navigation", { name: "Settings sections" });
    const playback = sections.getByRole("button", { name: "Playback", exact: true });
    await playback.hover();
    await expect(playback).toHaveCSS("background-color", fill);
    await expect(playback).toHaveCSS("color", ink);
    await playback.click();
    const loop = page.getByRole("switch", { name: "Loop forecast animation", exact: true });
    await loop.hover();
    await expect(loop).toHaveCSS("background-color", fill);
    await expect(loop).toHaveCSS("color", ink);
    await sections.getByRole("button", { name: "Units", exact: true }).click();
    const unit = page.getByRole("radio", { name: "°F Fahrenheit", exact: true }).locator("..");
    await unit.hover();
    await expect(unit).toHaveCSS("background-color", fill);
    await expect(unit).toHaveCSS("color", ink);
    await unit.click();
    const reset = page.getByRole("button", { name: "Reset units", exact: true });
    await reset.hover();
    await expect(reset).toHaveCSS("background-color", fill);
    await expect(reset).toHaveCSS("color", ink);
    await saveScreenshot(page, `orange-hover-${theme}-1920x1080.png`);
    await reset.click();
    await rail.getByRole("button", { name: "Model Viewer", exact: true }).click();
  }
  expectWarnings(consoleMessages, []);
});

for (const override of ["high", "balanced", "low"] as const) {
  test(`the floating controls use the ${override} glass profile`, async ({ page, consoleMessages }) => {
    await presetPreferences(page, { graphicsOverride: override });
    await page.goto("/");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMark(page, MARKS.stationsRendered);
    expect(await effectiveProfile(page)).toEqual({ mode: "full", profile: override, reasons: ["Chosen in Settings"] });
    const glass = await toolbarGlass(page);
    if (override === "low") {
      expect(glass.filter).toBe("none");
      expect(glass.alpha).toBeGreaterThan(0.9);
    } else {
      expect(glass.filter).toMatch(/blur\([1-9][\d.]*px\)/u);
      expect(glass.alpha).toBeGreaterThan(0);
      expect(glass.alpha).toBeLessThan(0.9);
    }
    expectWarnings(consoleMessages, []);
  });
}

test("ids and counts use tabular numerals", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMark(page, MARKS.stationsRendered);
  await chooseThroughSearch(page, "72357");
  const numeric = async (locator: ReturnType<Page["getByTestId"]>): Promise<string> =>
    locator.evaluate((element) => getComputedStyle(element).fontVariantNumeric);
  expect(await numeric(page.getByRole("complementary", { name: "Inspector" }).getByText("72357", { exact: true }))).toBe(
    "tabular-nums",
  );
  expectWarnings(consoleMessages, []);
});

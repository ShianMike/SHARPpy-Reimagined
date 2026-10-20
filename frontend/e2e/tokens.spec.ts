// Design tokens as shipped (DESIGN-tooling T8): computed :root tokens, the
// glass fill and filter per forced profile, and tabular numerals on ids.
import type { Page } from "@playwright/test";
import appManifest from "../package.json" with { type: "json" };
import { effectiveProfile, MARKS, presetPreferences, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { toolbarGlass } from "./support/glass";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { chooseThroughSearch } from "./support/ui";

/** A token resolved through a real property, so the minifier's spelling does not matter. */
async function resolved(page: Page, token: string, property: "color" | "width" | "z-index"): Promise<string> {
  return page.evaluate(
    ({ name, prop }) => {
      const probe = document.createElement("div");
      probe.style.setProperty("position", "absolute");
      probe.style.setProperty(prop, `var(${name})`);
      document.body.append(probe);
      const value = getComputedStyle(probe).getPropertyValue(prop);
      probe.remove();
      return value;
    },
    { name: token, prop: property },
  );
}

test("the :root tokens resolve to the design values", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMark(page, MARKS.shellPainted);
  expect(await resolved(page, "--surface-0", "color")).toBe("rgb(20, 21, 22)");
  expect(await resolved(page, "--text-primary", "color")).toBe("rgb(242, 242, 242)");
  expect(await resolved(page, "--accent", "color")).toBe("rgb(184, 84, 11)");
  expect(await resolved(page, "--warning", "color")).toBe("rgb(210, 153, 34)");
  expect(await resolved(page, "--space-2", "width")).toBe("8px");
  expect(await resolved(page, "--toolbar-h", "width")).toBe("56px");
  expect(await resolved(page, "--dock-w", "width")).toBe("56px");
  expect(await resolved(page, "--z-notice", "z-index")).toBe("15");
  expect(await resolved(page, "--z-popover", "z-index")).toBe("30");
  const toolbarHeight = await page.getByRole("banner", { name: "SHARPpy Reimagined" }).evaluate((element) => element.getBoundingClientRect().height);
  expect(toolbarHeight).toBe(56);
  expectWarnings(consoleMessages, []);
});

test("shared controls and local overrides use orange hover in both themes", async ({ page, consoleMessages }) => {
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
    await page.getByRole("button", { name: "Forecast Hours", exact: true }).click();
    const downloads = page.getByRole("dialog", { name: "Forecast Hours", exact: true });
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
    const unit = page.getByText("Fahrenheit", { exact: true }).locator("..");
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
    expect(glass).toMatchObject({ red: 25, green: 31, blue: 40, alpha: { high: 0.62, balanced: 0.68, low: 0.97 }[override] });
    expect(glass.filter).toBe({ high: "blur(6px) saturate(1.12)", balanced: "blur(4px) saturate(1.08)", low: "none" }[override]);
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

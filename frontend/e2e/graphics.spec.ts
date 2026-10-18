// Graphics profiles and operating-system preferences (DESIGN-tooling T8,
// DESIGN-frontend F6, F7): the Low profile has no backdrop filter anywhere;
// reduced transparency makes glass opaque under Automatic and under a forced
// High; the no-backdrop-filter fallback ships in the built CSS; and reduced
// motion zeroes transitions and the camera duration.
import type { Page } from "@playwright/test";
import { camera, MARKS, nextFrames, presetPreferences, waitForMapDrawn, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { elementsWithBackdropFilter, toolbarGlass } from "./support/glass";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";

/** 72357 in the golden catalogue. */
const NORMAN = { longitude: -97.43787, latitude: 35.18095 };

async function emulateReducedTransparency(page: Page): Promise<void> {
  const session = await page.context().newCDPSession(page);
  await session.send("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-transparency", value: "reduce" }],
  });
}

test("the Low profile draws no backdrop filter on any element", async ({ page, consoleMessages }) => {
  await presetPreferences(page, { graphicsOverride: "low" });
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  expect(await elementsWithBackdropFilter(page)).toEqual([]);
  const glass = await toolbarGlass(page);
  expect(glass.filter).toBe("none");
  await nextFrames(page);
  await saveScreenshot(page, "browser-low-profile.png");
  expectWarnings(consoleMessages, []);
});

for (const override of ["auto", "high"] as const) {
  test(`reduced transparency makes glass opaque under the ${override} profile`, async ({ page, consoleMessages }) => {
    await emulateReducedTransparency(page);
    await presetPreferences(page, { graphicsOverride: override });
    await page.goto("/");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMapDrawn(page);
    expect(await page.evaluate(() => matchMedia("(prefers-reduced-transparency: reduce)").matches)).toBe(true);
    const glass = await toolbarGlass(page);
    expect(glass).toEqual({ filter: "none", red: 29, green: 31, blue: 33, alpha: 1 });
    expect(await elementsWithBackdropFilter(page)).toEqual([]);
    if (override === "high") {
      expect(await page.evaluate(() => document.documentElement.dataset.graphicsProfile)).toBe("high");
      await nextFrames(page);
      await saveScreenshot(page, "browser-reduced-transparency.png");
    }
    expectWarnings(consoleMessages, []);
  });
}

test("the built CSS ships the no-backdrop-filter fallback", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMark(page, MARKS.shellPainted);
  const found = await page.evaluate(() => {
    const results: { condition: string; supported: boolean; fill: string; filter: string }[] = [];
    for (const sheet of Array.from(document.styleSheets)) {
      for (const rule of Array.from(sheet.cssRules)) {
        if (rule instanceof CSSSupportsRule && rule.conditionText.includes("backdrop-filter")) {
          for (const inner of Array.from(rule.cssRules)) {
            if (inner instanceof CSSStyleRule && inner.selectorText === ":root[data-graphics-profile]") {
              results.push({
                condition: rule.conditionText,
                supported: CSS.supports(rule.conditionText),
                fill: inner.style.getPropertyValue("--glass-fill").trim(),
                filter: inner.style.getPropertyValue("--glass-filter").trim(),
              });
            }
          }
        }
      }
    }
    return results;
  });
  expect(found).toHaveLength(1);
  expect(found[0]?.condition).toMatch(/^not \(/u);
  expect(found[0]?.supported).toBe(false);
  expect(found[0]?.fill).toBe("var(--surface-1)");
  expect(found[0]?.filter).toBe("none");
  expectWarnings(consoleMessages, []);
});

test("reduced motion: zero-duration transitions and a one-frame camera move", async ({ page, consoleMessages }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  // The minifier ships the token as 0s (PROGRESS I15).
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--motion-camera").trim())).toBe(
    "0s",
  );
  const rail = page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer" });
  expect(await rail.evaluate((element) => getComputedStyle(element).transitionDuration.split(", ").every((d) => d === "0s"))).toBe(
    true,
  );
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: "Search stations" }).fill("72357");
  await expect(page.getByRole("listbox", { name: "Station results" })).toBeVisible();
  // Enter, then read the camera on the next animation frame.
  const next = await page.evaluate(
    () =>
      new Promise<{ center: [number, number]; zoom: number } | null>((resolve) => {
        const input = document.getElementById("station-search");
        input?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
        requestAnimationFrame(() => {
          resolve(window.__SHARPPY_E2E__?.camera() ?? null);
        });
      }),
  );
  expect(next).not.toBeNull();
  expect(Math.abs((next?.center[0] ?? 0) - NORMAN.longitude)).toBeLessThan(0.01);
  expect(Math.abs((next?.center[1] ?? 0) - NORMAN.latitude)).toBeLessThan(0.01);
  expect((await camera(page))?.zoom).toBeGreaterThanOrEqual(5);
  expectWarnings(consoleMessages, []);
});

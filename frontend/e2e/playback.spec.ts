import { presetPreferences, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot, screenshotPath } from "./support/screenshots";
import { turnOn } from "./support/ui";
import { expect, test } from "./support/test";
import appManifest from "../package.json" with { type: "json" };

test.describe("forecast playback", () => {
  for (const panels of [1, 2, 4]) test(`playback keeps ${String(panels)} panels loaded while advancing, and pauses`, async ({ page, consoleMessages }) => {
    test.setTimeout(120_000);
    // Keep this functional check within SwiftShader's budget; rendering quality and auxiliary layers have separate specs.
    const layers = { "model-field": { visible: true, opacity: 0.5, smoothing: false } };
    await presetPreferences(page, { graphicsOverride: "low", productKey: "tmp-2m", layers,
      panels: ["dpt-2m", "rh-2m", "mlcape"].map((productKey) => ({ productKey, layers })) });
    await page.addInitScript((currentVersion) => {
      localStorage.setItem("sharppy-workspace-ui", JSON.stringify({ state: { lastVersion: currentVersion }, version: 0 }));
    }, appManifest.version);
    // Install before app timers exist; the pause target exceeds this test's entire timeout.
    if (panels === 4) await page.clock.install({ time: new Date("2026-10-04T20:00:00Z") });
    await page.goto("/");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMapDrawn(page);
    await turnOn(page, "model-field");
    // Jump before the auxiliary renderers start, so their real start-up deadlines are not skipped.
    if (panels === 4) await page.clock.pauseAt(new Date("2026-10-04T21:00:01Z"));
    if (panels > 1) {
      await page.getByRole("toolbar", { name: "Map" }).getByRole("button", { name: panels === 2 ? "Two synchronized panels" : "Four synchronized panels" }).click();
    }
    const hour = page.getByRole("slider", { name: "Forecast hour", exact: true });
    const timeline = page.getByRole("region", { name: "Forecast timeline" });
    const valid = page.getByLabel("Valid time");
    if (panels === 4) {
      const hoursButton = timeline.getByRole("button", { name: "Forecast Hours", exact: true });
      await hoursButton.press("Enter");
      const details = page.getByRole("dialog", { name: "Forecast Hours", exact: true });
      const missing = details.locator("details");
      await expect(missing).not.toHaveAttribute("open");
      await missing.locator("summary").hover();
      const accent = await hoursButton.evaluate((button) => getComputedStyle(button).color);
      await expect(missing.locator("summary")).toHaveCSS("color", accent);
      expect(await missing.evaluate((element) => getComputedStyle(element, "::details-content").transitionProperty)).toContain("block-size");
      await missing.locator("summary").press("Enter");
      await expect(missing.getByText("F018", { exact: true })).toBeVisible();
      await missing.locator("summary").press("Enter");
      await details.getByRole("button", { name: "Load Hours", exact: true }).press("Enter");
      // Drive paused fixture timers; hosted software rendering can spend seconds per clock advance.
      await expect.poll(async () => {
        await page.clock.fastForward(300);
        return timeline.getByRole("status", { name: "Forecast downloads" }).textContent();
      }, { intervals: [0], timeout: 60_000 }).toContain("All 19 forecast hours saved to cache");
      await expect(hoursButton).toContainText("19/19");
      await expect(details.getByText("Saved On Device", { exact: true }).locator("..")).toContainText("19 / 19");
      await expect(details.getByText("Ready In Memory", { exact: true })).toBeVisible();
      await expect(details.getByRole("button", { name: "Load Hours", exact: true })).toHaveCount(0);
      await details.press("Escape");
      await expect(hoursButton).toBeFocused();
      await timeline.getByRole("button", { name: "Smooth Playback", exact: true }).press("Enter");
      await expect(timeline.getByRole("button", { name: "Smooth Playback", exact: true })).toHaveAttribute("aria-pressed", "true");
    }
    await expect(valid).toHaveAttribute("aria-busy", "false");
    if (panels === 4) {
      await timeline.getByRole("button", { name: "Play", exact: true }).press("Enter");
      for (let frame = 1; frame <= 3; frame++) {
        await page.clock.fastForward(750);
        await expect(hour).toHaveValue(String(frame));
      }
    } else {
      await timeline.getByRole("button", { name: "Play", exact: true }).click();
      await expect.poll(async () => Number(await hour.inputValue()), { timeout: 60_000 }).toBeGreaterThanOrEqual(3);
    }
    // Take one stable frame before checking every panel; playback can advance between serial assertions.
    await timeline.getByRole("button", { name: "Pause", exact: true }).press("Enter");
    await expect(valid).toHaveAttribute("aria-busy", "false");
    for (let panel = 1; panels > 1 && panel <= panels; panel++) {
      await expect(page.getByTestId(`panel-chip-${String(panel)}`)).not.toContainText("Loading");
    }
    const stopped = await hour.inputValue();
    if (panels === 4) await page.clock.fastForward(1500);
    else await page.waitForTimeout(1500);
    expect(await hour.inputValue()).toBe(stopped);
    await expect(page.getByTestId("field-shown")).toContainText(`F${stopped.padStart(3, "0")}`);
    if (panels === 4) {
      await page.clock.resume();
      await saveScreenshot(page, "browser-four-panel-playback.png");
      await page.setViewportSize({ width: 1920, height: 1080 });
      const hoursButton = timeline.getByRole("button", { name: "Forecast Hours", exact: true });
      await hoursButton.press("Enter");
      const details = page.getByRole("dialog", { name: "Forecast Hours", exact: true });
      await expect(details).toBeVisible();
      expect(await details.evaluate((panel) => panel.getBoundingClientRect().bottom)).toBeLessThan((await hoursButton.boundingBox())?.y ?? 0);
      await saveScreenshot(page, "browser-forecast-hours-desktop.png");
      await details.press("Escape");
      await page.setViewportSize({ width: 720, height: 800 });
      await expect(valid).toBeHidden();
      expect(await hoursButton.evaluate((button) => button.getBoundingClientRect().right <= (button.closest("section")?.getBoundingClientRect().right ?? 0))).toBe(true);
      await saveScreenshot(page, "browser-playback-narrow.png");
      await hoursButton.press("Enter");
      await expect(details.getByRole("button", { name: "Check Cache", exact: true })).toBeVisible();
      const bounds = await details.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds?.x).toBeGreaterThanOrEqual(0);
      expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(720);
      await saveScreenshot(page, "browser-forecast-hours-narrow.png");
    }
    expectWarnings(consoleMessages, []);
  });

});

test("forecast-hour disclosure animates while its bottom edge stays anchored", async ({ page, consoleMessages }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await presetPreferences(page, { graphicsOverride: "low", productKey: "tmp-2m",
    layers: { "model-field": { visible: true, opacity: 0.5, smoothing: false } } });
  await page.addInitScript((version) => {
    localStorage.setItem("sharppy-workspace-ui", JSON.stringify({ state: { lastVersion: version }, version: 0 }));
  }, appManifest.version);
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  const trigger = page.getByRole("button", { name: "Forecast Hours", exact: true });
  await trigger.click();
  const details = page.getByRole("dialog", { name: "Forecast Hours", exact: true });
  const summary = details.locator("details > summary");
  await summary.hover();
  await expect(summary).toHaveCSS("color", await trigger.evaluate((button) => getComputedStyle(button).color));
  for (const phase of ["expand", "collapse"]) {
    const frames = await summary.evaluate(async (element) => {
      const panel = element.closest('[role="dialog"]');
      if (panel === null) throw new Error("Missing download panel");
      const before = panel.getBoundingClientRect();
      const samples = [{ height: before.height, bottom: before.bottom }];
      (element as HTMLElement).click();
      for (let frame = 0; frame < 20; frame++) {
        await new Promise<void>((resolve) => { requestAnimationFrame(() => { resolve(); }); });
        const box = panel.getBoundingClientRect();
        samples.push({ height: box.height, bottom: box.bottom });
      }
      return samples;
    });
    const start = frames[0]?.height ?? 0, end = frames.at(-1)?.height ?? 0;
    expect(Math.abs(end - start), phase).toBeGreaterThan(20);
    expect(frames.some(({ height }) => height > Math.min(start, end) + 1 && height < Math.max(start, end) - 1), phase).toBe(true);
    expect(Math.max(...frames.map(({ bottom }) => Math.abs(bottom - (frames[0]?.bottom ?? 0)))), phase).toBeLessThan(1);
    if (phase === "expand") {
      await details.screenshot({ path: screenshotPath("forecast-hours-panel-expanded.png"), animations: "disabled" });
      await saveScreenshot(page, "forecast-hours-motion-1920x1080.png");
    }
  }
  await summary.press("Enter");
  await summary.press("Enter");
  await expect(details.locator("details")).not.toHaveAttribute("open");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await summary.press("Enter");
  expect(await summary.evaluate((element) => getComputedStyle(element.parentElement as HTMLElement, "::details-content").transitionDuration)).toBe("0s, 0s, 0s");
  await details.press("Escape");
  await expect(trigger).toBeFocused();
  expectWarnings(consoleMessages, []);
});

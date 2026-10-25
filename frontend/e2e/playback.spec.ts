import { presetPreferences, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { layerRow, turnOn } from "./support/ui";
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
      const hoursButton = page.getByRole("button", { name: "Choose forecast hour", exact: true });
      await hoursButton.press("Enter");
      const details = page.getByRole("dialog", { name: "Forecast hour", exact: true });
      await expect(timeline.getByRole("button", { name: "Forecast Hours", exact: true })).toHaveCount(0);
      await expect(details.getByRole("button", { name: "Select forecast hour 18", exact: true })).toBeVisible();
      await details.getByRole("button", { name: "Load Hours", exact: true }).press("Enter");
      await expect(details.locator('[data-hour-state="loading"]').first()).toHaveCSS("border-color", "rgb(88, 166, 255)");
      // Drive paused fixture timers; hosted software rendering can spend seconds per clock advance.
      await expect.poll(async () => {
        await page.clock.fastForward(300);
        return page.getByRole("status", { name: "Forecast downloads" }).textContent();
      }, { intervals: [0], timeout: 60_000 }).toContain("All 19 forecast hours saved to cache");
      await expect(details.getByLabel("Forecast cache summary")).toContainText("19 saved");
      await expect(details.getByLabel("Forecast cache summary")).toContainText("ready");
      await expect(details.getByRole("button", { name: "Select forecast hour 0", exact: true })).toHaveCSS("border-color", "rgb(63, 185, 80)");
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
    const field = await layerRow(page, "model-field");
    await expect(field.getByTestId("field-shown")).toContainText(`F${stopped.padStart(3, "0")}`);
    await page.getByRole("button", { name: "Close layers", exact: true }).click();
    if (panels === 4) {
      await page.clock.resume();
      await saveScreenshot(page, "browser-four-panel-playback.png");
      await page.setViewportSize({ width: 1920, height: 1080 });
      const hoursButton = page.getByRole("button", { name: "Choose forecast hour", exact: true });
      await hoursButton.press("Enter");
      const details = page.getByRole("dialog", { name: "Forecast hour", exact: true });
      await expect(details).toBeVisible();
      expect(await details.evaluate((panel) => Math.abs(panel.getBoundingClientRect().x - (innerWidth - panel.getBoundingClientRect().width) / 2))).toBeLessThan(1);
      await saveScreenshot(page, "browser-forecast-hours-desktop.png");
      await details.press("Escape");
      await page.setViewportSize({ width: 720, height: 800 });
      await expect(valid).toBeHidden();
      expect(await hoursButton.evaluate((button) => button.getBoundingClientRect().right <= innerWidth)).toBe(true);
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

test("forecast-hour picker keeps one popup with a fixed footer and scrollable hour list", async ({ page, consoleMessages }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await presetPreferences(page, { graphicsOverride: "low", productKey: "tmp-2m",
    layers: { "model-field": { visible: true, opacity: 0.5, smoothing: false } } });
  await page.addInitScript((version) => {
    localStorage.setItem("sharppy-workspace-ui", JSON.stringify({ state: { lastVersion: version }, version: 0 }));
  }, appManifest.version);
  await page.goto("/");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  const trigger = page.getByRole("button", { name: "Choose forecast hour", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Forecast hour", exact: true });
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Forecast Hours", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Load Hours", exact: true })).toBeVisible();
  await dialog.evaluate(async (panel) => { await Promise.allSettled(panel.getAnimations().map((animation) => animation.finished)); });
  expect(await dialog.evaluate((panel) => panel.scrollHeight <= panel.clientHeight)).toBe(true);
  const before = await dialog.getByRole("button", { name: "Load Hours", exact: true }).boundingBox();
  await dialog.getByRole("button", { name: "Select forecast hour 18", exact: true }).evaluate((hour) => { hour.scrollIntoView({ block: "nearest" }); });
  const after = await dialog.getByRole("button", { name: "Load Hours", exact: true }).boundingBox();
  expect(after?.y).toBe(before?.y);
  await saveScreenshot(page, "browser-forecast-hour-cache-status.png");
  await dialog.press("Escape");
  await expect(trigger).toBeFocused();
  expectWarnings(consoleMessages, []);
});

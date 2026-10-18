import { presetPreferences, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { turnOn } from "./support/ui";
import { expect, test } from "./support/test";

test.describe("forecast playback", () => {
  for (const panels of [1, 2, 4]) test(`playback keeps ${String(panels)} panels loaded while advancing, and pauses`, async ({ page, consoleMessages }) => {
    test.setTimeout(120_000);
    // Keep this functional check within SwiftShader's budget; rendering quality and auxiliary layers have separate specs.
    const layers = { "model-field": { visible: true, opacity: 0.5, smoothing: false } };
    await presetPreferences(page, { graphicsOverride: "low", productKey: "tmp-2m", layers,
      panels: ["dpt-2m", "rh-2m", "mlcape"].map((productKey) => ({ productKey, layers })) });
    await page.goto("/");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMapDrawn(page);
    await turnOn(page, "model-field");
    if (panels > 1) {
      await page.getByRole("toolbar", { name: "Map" }).getByRole("button", { name: panels === 2 ? "Two synchronized panels" : "Four synchronized panels" }).click();
    }
    const hour = page.getByRole("slider", { name: "Forecast hour", exact: true });
    const timeline = page.getByRole("region", { name: "Forecast timeline" });
    const valid = page.getByLabel("Valid time");
    await expect(valid).toHaveAttribute("aria-busy", "false");
    if (panels === 4) {
      await timeline.getByRole("button", { name: "Download all forecast hours", exact: true }).click();
      await expect(timeline.getByRole("status", { name: "Forecast downloads" })).toContainText("All 19 forecast hours downloaded");
      await expect(timeline.getByRole("button", { name: "Download all forecast hours", exact: true })).toContainText("19/19");
      await timeline.getByRole("button", { name: "Smooth playback", exact: true }).click();
      await expect(timeline.getByRole("button", { name: "Smooth playback", exact: true })).toHaveAttribute("aria-pressed", "true");
      // Install after real map startup; advance the app's timers without benchmarking software-GPU frames.
      await page.clock.install({ time: new Date("2026-10-04T21:00:00Z") });
      await page.clock.pauseAt(new Date("2026-10-04T21:00:01Z"));
    }
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
      await page.setViewportSize({ width: 720, height: 800 });
      await expect(valid).toBeHidden();
      expect(await timeline.getByRole("button", { name: "Smooth playback", exact: true }).evaluate((button) => button.getBoundingClientRect().right <= (button.closest("section")?.getBoundingClientRect().right ?? 0))).toBe(true);
      await saveScreenshot(page, "browser-playback-narrow.png");
    }
    expectWarnings(consoleMessages, []);
  });

});

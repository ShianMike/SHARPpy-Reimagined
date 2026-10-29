// Opt-in, real installed WebView2 acceptance; no transport or provider mocks.
import { appendFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { FIELD_MS, FOUR_PRODUCTS, page, results, setupMapHarness, chooseProduct, setLayer,
  loadedField, loadedPanels, forecastIdentity, mapCentre, fieldOutcome, shot } from "./mapSupport";

const phase = process.env.SHARPPY_ACCEPTANCE_PHASE;
const previousVersion = process.env.SHARPPY_ACCEPTANCE_PREVIOUS_VERSION;
const expectedVersion = process.env.SHARPPY_ACCEPTANCE_EXPECTED_VERSION;
const output = process.env.E2E_SCREENSHOT_DIR;
const workspaces = () => page.getByRole("navigation", { name: "Workspaces" });
const sections = () => page.getByRole("navigation", { name: "Settings sections" });
const snapshot = async () => page.evaluate(() => ({
  ui: JSON.parse(localStorage.getItem("sharppy-workspace-ui") ?? "null") as unknown,
  workspace: JSON.parse(localStorage.getItem("sharppy.prefs.v1") ?? "null") as unknown,
}));

test.describe("installed native acceptance", () => {
  setupMapHarness();
  test.skip(phase === undefined, "Only run by the opt-in native acceptance workflow");

  test("preferences survive installation and repeated native restart", async () => {
    test.skip(phase === "profile");
    if (phase === "update") test.setTimeout(6 * 60_000);
    if (previousVersion === undefined || expectedVersion === undefined) throw new Error("The native acceptance script must provide both versions");
    const version = phase === "seed" || phase === "update" ? previousVersion : expectedVersion;
    expect(version).toMatch(/^\d+\.\d+\.\d+$/u);
    const notes = page.getByRole("dialog", { name: `What’s new in ${version}` });
    await expect(workspaces()).toBeVisible();
    if (phase === "upgrade") {
      await expect(notes).toBeVisible();
      await shot("upgraded-highlights.png", true);
      await notes.getByRole("button", { name: "Got it", exact: true }).click();
    } else if (phase === "restart") {
      await expect(notes).toBeHidden();
    } else if (await notes.isVisible()) {
      await notes.getByRole("button", { name: "Got it", exact: true }).click();
    }
    await workspaces().getByRole("button", { name: "Settings", exact: true }).click();
    await sections().getByRole("button", { name: "Updates", exact: true }).click();
    await expect(page.getByText(`Current version: ${version}`, { exact: true })).toBeVisible();
    results.version = version;
    await sections().getByRole("button", { name: "Appearance", exact: true }).click();
    if (phase === "seed") await page.getByRole("radio", { name: /Light/u }).check();
    await expect(page.getByRole("radio", { name: /Light/u })).toBeChecked();
    await sections().getByRole("button", { name: "Units", exact: true }).click();
    if (phase === "seed") {
      await page.getByRole("radio", { name: "°C Celsius", exact: true }).check();
      await page.getByRole("radio", { name: "km/h Kilometres/h", exact: true }).check();
    }
    await expect(page.getByRole("radio", { name: "°C Celsius", exact: true })).toBeChecked();
    await expect(page.getByRole("radio", { name: "km/h Kilometres/h", exact: true })).toBeChecked();
    await sections().getByRole("button", { name: "Playback", exact: true }).click();
    if (phase === "seed") await page.getByRole("radio", { name: "2×", exact: true }).check();
    await expect(page.getByRole("radio", { name: "2×", exact: true })).toBeChecked();
    await workspaces().getByRole("button", { name: "Model Viewer", exact: true }).click();
    if (phase === "seed") await page.getByRole("toolbar", { name: "Map", exact: true }).getByRole("button", { name: "Four synchronized panels", exact: true }).click();
    await expect(page.locator('[data-testid^="map-panel-"]')).toHaveCount(4);
    await page.waitForTimeout(1_000);
    results.preferences = await snapshot();
    await shot(`${phase ?? "unknown"}-preferences.png`, true);
    if (phase === "update") {
      await workspaces().getByRole("button", { name: "Settings", exact: true }).click();
      await sections().getByRole("button", { name: "Updates", exact: true }).click();
      await page.getByRole("button", { name: "Check for updates", exact: true }).click();
      await expect(page.getByText(`Version ${expectedVersion} is available`, { exact: true })).toBeVisible({ timeout: 40_000 });
      await shot("interactive-update-available.png", true);
      // The real signed updater exits this process after launching NSIS.
      await page.getByRole("button", { name: "Install update", exact: true }).click();
      await expect.poll(() => page.isClosed(), { timeout: 300_000, intervals: [1_000] }).toBe(true);
    }
  });

  test("diagnose native renderer retention", async () => {
    test.skip(phase !== "profile");
    test.setTimeout(25 * 60_000);
    await workspaces().getByRole("button", { name: "Settings", exact: true }).click();
    await sections().getByRole("button", { name: "Device Info", exact: true }).click();
    results.device = await page.getByTestId("settings-view").innerText();
    results.window = await page.evaluate(() => ({ outerWidth, outerHeight, innerWidth, innerHeight, devicePixelRatio }));
    await sections().getByRole("button", { name: "Units", exact: true }).click();
    await page.getByRole("radio", { name: "Auto Source units", exact: true }).check();
    await workspaces().getByRole("button", { name: "Model Viewer", exact: true }).click();
    const forecast = page.getByRole("group", { name: "Forecast", exact: true });
    const timeline = page.getByRole("region", { name: "Forecast timeline", exact: true });
    await forecast.getByRole("button", { name: "Choose model", exact: true }).click();
    await page.getByRole("button", { name: "Choose HRRR", exact: true }).click();
    await forecast.getByRole("button", { name: "Choose run", exact: true }).click();
    await page.getByRole("button", { name: "Use latest suggested run", exact: true }).click();
    await forecast.getByRole("button", { name: "Choose forecast hour", exact: true }).click();
    await page.getByRole("button", { name: "Select forecast hour 0", exact: true }).click();
    await setLayer("model-field", true);
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const outcome = await fieldOutcome();
      if (outcome.ok) break;
      expect(attempt, outcome.text).toBeLessThan(2);
      await forecast.getByRole("button", { name: "Choose run", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Model run", exact: true });
      const runs = dialog.getByRole("button", { name: /^Run \d\d-\d\d \d\dZ$/u });
      const index = (await runs.evaluateAll((buttons) => buttons.map((button) => button.getAttribute("aria-pressed")))).indexOf("true");
      expect(index).toBeGreaterThanOrEqual(0);
      if (index === 0) {
        await dialog.getByRole("button", { name: "Previous run date", exact: true }).click();
        await runs.last().click();
      } else await runs.nth(index - 1).click();
    }
    for (const [index, product] of FOUR_PRODUCTS.entries()) {
      await page.getByRole("group", { name: "Active map panel" }).getByRole("button", { name: `Panel ${String(index + 1)}`, exact: true }).click();
      await chooseProduct(product.label, product.unit);
      await setLayer("model-field", true);
      await loadedField();
    }
    results.initialPanels = await loadedPanels();
    const cdp = await page.context().newCDPSession(page);
    const heapSnapshot = async (name: string): Promise<void> => {
      if (output === undefined) throw new Error("Heap profiling requires an evidence directory");
      const path = join(output, `${name}.heapsnapshot`);
      writeFileSync(path, "");
      const chunk = ({ chunk }: { chunk: string }): void => { appendFileSync(path, chunk); };
      cdp.on("HeapProfiler.addHeapSnapshotChunk", chunk);
      try { await cdp.send("HeapProfiler.takeHeapSnapshot", { reportProgress: false }); }
      finally { cdp.off("HeapProfiler.addHeapSnapshotChunk", chunk); }
    };
    await heapSnapshot("renderer-initial");
    const start = Date.now();
    let cycle = 0;
    let capture = 0;
    const errors: string[] = [];
    page.on("pageerror", (error) => { errors.push(String(error)); });
    while (Date.now() - start < 15 * 60_000) {
      const since = Date.now();
      const before = await forecastIdentity();
      const hour = page.getByRole("slider", { name: "Forecast hour", exact: true });
      const initial = await hour.inputValue();
      await timeline.getByRole("button", { name: "Play", exact: true }).click();
      try {
        await expect.poll(async () => hour.inputValue(), { timeout: 2 * FIELD_MS, intervals: [250] }).not.toBe(initial);
      } finally {
        await timeline.getByRole("button", { name: "Pause", exact: true }).click();
      }
      const panels = await loadedPanels();
      const centre = await mapCentre();
      const measure = capture === 0;
      if (measure && output !== undefined) {
        writeFileSync(join(output, `capture-${String(capture++)}-ready.json`), JSON.stringify({ utc: new Date().toISOString(), panels, forecast: await forecastIdentity() }));
      }
      // Continuous native panning gives PresentMon real displayed frames to measure.
      const panUntil = Date.now() + (measure ? 180_000 : 30_000);
      for (let step = 0; Date.now() < panUntil; step += 1) {
        await page.mouse.move(centre.x + (step % 2 === 0 ? -80 : 80), centre.y);
        await page.mouse.down();
        await page.mouse.move(centre.x + (step % 2 === 0 ? 80 : -80), centre.y, { steps: 4 });
        await page.mouse.up();
        await page.waitForTimeout(100);
      }
      if (cycle % 3 === 0) {
        await workspaces().getByRole("button", { name: "Soundings", exact: true }).click();
        await page.getByRole("tab", { name: "File", exact: true }).click();
        await page.locator('input[type="file"]').setInputFiles(join("..", "examples", "soundings", "hrrr_kbvo_20260625_06z.buf"));
        await page.getByTestId("open-file").click();
        const image = page.getByTestId("sounding-viewer").getByTestId("sounding-image");
        await expect(image).toBeVisible({ timeout: 240_000 });
        await expect.poll(async () => image.evaluate((element) => (element as HTMLImageElement).naturalWidth), { timeout: 240_000 }).toBeGreaterThan(0);
        await page.getByRole("button", { name: "Show sounding controls", exact: true }).click();
        const time = page.getByRole("combobox", { name: "Sounding time (UTC)" });
        await time.click();
        await page.keyboard.press("End");
        await page.keyboard.press("Enter");
        await expect(time).toHaveAttribute("value", "48");
        await expect(page.getByTestId("sounding-viewer")).toHaveAttribute("aria-description", /time 49 of 49/u);
        await page.getByRole("button", { name: "Show sounding sources", exact: true }).click();
        await page.getByTestId("open-soundings").getByRole("button", { name: /^Close /u }).first().click();
        await expect(page.getByTestId("open-soundings").getByRole("listitem")).toHaveCount(0);
        await workspaces().getByRole("button", { name: "Live Tracking", exact: true }).click();
        await setLayer("radar", true);
        await expect(page.getByTestId("radar-shown")).toBeVisible({ timeout: 90_000 });
        await workspaces().getByRole("button", { name: "Model Viewer", exact: true }).click();
      }
      if (cycle % 4 === 0) {
        for (const model of ["NAM 3km CONUS", "HRRR"]) {
          await forecast.getByRole("button", { name: "Choose model", exact: true }).click();
          await page.getByRole("button", { name: `Choose ${model}`, exact: true }).click();
        }
      }
      await forecast.getByRole("button", { name: "Choose forecast hour", exact: true }).click();
      await page.getByRole("button", { name: `Select forecast hour ${String(cycle % 3)}`, exact: true }).click();
      await loadedPanels();
      const heap = await cdp.send("Runtime.getHeapUsage") as { usedSize: number; totalSize: number; embedderHeapUsedSize?: number; backingStorageSize?: number };
      const sample = { utc: new Date().toISOString(), elapsedSeconds: (Date.now() - start) / 1_000,
        cycle: ++cycle, before, panels, errors: [...errors], heap };
      if (output !== undefined) appendFileSync(join(output, "workload.ndjson"), `${JSON.stringify(sample)}\n`);
      expect(errors).toEqual([]);
      console.log(`Native heap diagnostic: cycle ${String(cycle)} complete; ${String(Math.floor(sample.elapsedSeconds / 60))}m ${String(Math.floor(sample.elapsedSeconds % 60))}s elapsed of 15 minutes; JS heap ${String(Math.round(heap.usedSize / 2**20))} MiB, backing storage ${heap.backingStorageSize === undefined ? "unavailable" : String(Math.round(heap.backingStorageSize / 2**20)) + " MiB"}`);
      if (cycle % 5 === 0) await shot(`diagnostic-${String(cycle)}.png`);
      await page.waitForTimeout(Math.max(0, 120_000 - (Date.now() - since)));
    }
    results.diagnostic = { elapsedSeconds: (Date.now() - start) / 1_000, cycles: cycle, errors };
    await heapSnapshot("renderer-final");
    if (output !== undefined) writeFileSync(join(output, "diagnostic-complete.json"), JSON.stringify(results, null, 2));
    await cdp.detach();
    await shot("diagnostic-complete.png", true);
  });
});

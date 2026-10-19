import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, expect, test, type Browser, type Locator, type Page } from "@playwright/test";
import { addHours, compactUtc, forecastLabel } from "../../src/features/sounding/soundingTime";
import { nextFrames } from "../support/app";
import { regionStats } from "../support/pixels";

declare global {
  interface Window {
    __sharppyFrames?: number[] | undefined;
  }
}

const CDP = process.env.SHARPPY_DESKTOP_CDP;
const OUT = process.env.SHARPPY_MEASURE_OUT;
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
/** The Rust deadlines (field 120 s, outlook and radar 60 s, availability 35 s) plus slack. */
export const FIELD_MS = 150_000;
export const OVERLAY_MS = 90_000;
export const RUN_ATTEMPTS = 3;
export const FOUR_PRODUCTS = [
  { label: "2 m AGL Temperature", unit: "°F" },
  { label: "2 m AGL Dewpoint", unit: "°F" },
  { label: "2 m AGL Relative Humidity", unit: "%" },
  { label: "2 m AGL Theta-e, 10 m AGL Wind", unit: "K" },
] as const;

export const results: Record<string, unknown> = {};
let browser: Browser | undefined;
export let page: Page;

export async function shot(name: string, evidence = false): Promise<void> {
  if (SHOTS !== undefined || evidence) {
    const png = await page.screenshot({ path: test.info().outputPath(name), timeout: 10_000 });
    if (evidence) await test.info().attach(name, { body: png, contentType: "image/png" });
    if (SHOTS !== undefined) writeFileSync(join(SHOTS, name), png);
  }
}

export function seconds(since: number): number {
  return Math.round((Date.now() - since) / 100) / 10;
}

export const text = async (locator: Locator): Promise<string> => (await locator.innerText()).replace(/\s+/gu, " ").trim();

export function row(id: string): Locator {
  return page.getByTestId(`layer-row-${id}`);
}

export async function openLayers(): Promise<void> {
  const toggle = page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true });
  // The closing drawer stays visible during its discrete display transition.
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByTestId("layer-panel")).toBeVisible();
  await expect(page.getByTestId("layer-data-mode")).toHaveText("Live providers");
}

export async function setLayer(id: string, on: boolean): Promise<void> {
  await openLayers();
  const mapDetail = ["stations", "station-labels", "model-domain", "place-labels", "radar-site-labels", "basemap"].includes(id);
  await page.getByRole("group", { name: "Layer categories" }).getByRole("button", { name: mapDetail ? "Map details" : "Weather", exact: true }).click();
  const toggle = row(id).getByRole("switch").first();
  if ((await toggle.getAttribute("aria-checked")) !== String(on)) {
    await toggle.click();
  }
  await expect(toggle).toHaveAttribute("aria-checked", String(on));
  const settings = row(id).getByRole("button", { name: /settings$/u });
  if (on && await settings.count() > 0 && await settings.getAttribute("aria-expanded") === "false") await settings.click();
}

export async function chooseProduct(label: string, unit: string): Promise<void> {
  const picker = page.getByRole("group", { name: "Forecast", exact: true }).getByRole("button", { name: "Choose parameter", exact: true });
  await picker.click();
  await page.getByRole("dialog", { name: "Parameters", exact: true }).getByRole("button", { name: `${label}, ${unit}`, exact: true }).click();
  await expect(picker).toHaveAccessibleDescription(label);
}

/** Cross-check the full run date, hour and valid time exposed by the current UI. */
export async function forecastIdentity() {
  const toolbar = page.getByRole("group", { name: "Forecast", exact: true });
  await expect(toolbar.getByRole("button", { name: "Choose model", exact: true })).toHaveAccessibleDescription("HRRR");
  const run = await toolbar.getByRole("button", { name: "Choose run", exact: true }).getAttribute("aria-description");
  const selected = await page.getByRole("slider", { name: "Forecast hour", exact: true }).getAttribute("aria-valuetext");
  const hour = /^F(\d{3}) · valid /u.exec(selected ?? "")?.[1];
  if (run === null || !/^\d{4}-\d\d-\d\d \d\dZ$/u.test(run) || hour === undefined) throw new Error(`missing forecast identity: ${String(run)}, ${String(selected)}`);
  const runTime = `${run.slice(0, 10)}T${run.slice(11, 13)}:00:00Z`;
  const valid = compactUtc(addHours(runTime, Number(hour)));
  const frame = forecastLabel(Number(hour));
  expect(selected).toBe(`${frame} · valid ${valid} UTC`);
  await expect(toolbar.getByRole("button", { name: "Choose forecast hour", exact: true })).toContainText(frame);
  await expect(page.getByLabel("Valid time", { exact: true }).locator("strong")).toHaveText(`${valid} UTC`);
  return { run, hour: frame, valid, field: `${frame} of the ${compactUtc(runTime)} run, valid ${valid}.` };
}

/** Waits for the model field row to show its frame or an error; returns which and how long. */
export async function fieldOutcome() {
  const started = Date.now();
  const shown = row("model-field").getByTestId("field-shown");
  const failed = row("model-field").getByTestId("field-error");
  await expect.poll(async () => (await shown.isVisible()) || (await failed.isVisible()), { timeout: FIELD_MS, intervals: [250] }).toBe(true);
  const ok = await shown.isVisible();
  const forecast = await forecastIdentity();
  if (ok) {
    await expect(shown).toContainText(forecast.field);
    await expect(shown).toContainText("NOAA/NCEP HRRR");
  }
  return { ok, seconds: seconds(started), text: await text(ok ? shown : failed), forecast };
}

export async function loadedField() {
  const outcome = await fieldOutcome();
  expect(outcome.ok, outcome.text).toBe(true);
  return outcome;
}

/** The map's centre in viewport pixels. */
export async function mapCentre(panel = 1): Promise<{ x: number; y: number }> {
  const box = await page.getByTestId(`map-panel-${String(panel)}`).boundingBox();
  if (box === null) {
    throw new Error("no map box");
  }
  return { x: box.x + box.width * 0.45, y: box.y + box.height * 0.55 };
}

export async function pointerValue(panel: number, label: string, unit: string): Promise<string> {
  const centre = await mapCentre(panel);
  await page.mouse.move(centre.x, centre.y);
  const value = page.getByTestId(`map-panel-${String(panel)}`).getByTestId("pointer-value");
  await expect(value).toContainText(label);
  await expect(value.locator("strong")).toHaveText(/^-?\d+(?:\.\d+)? /u);
  await expect(value.locator("strong")).toContainText(unit);
  return text(value);
}

/** A title/scale alone also exists with no field; require every panel's current frame. */
export async function loadedPanels() {
  const panels = [];
  const forecast = await forecastIdentity();
  await openLayers();
  await page.getByRole("group", { name: "Layer categories" }).getByRole("button", { name: "Weather", exact: true }).click();
  for (const [index, product] of FOUR_PRODUCTS.entries()) {
    const panel = index + 1;
    await page.getByRole("group", { name: "Active map panel" }).getByRole("button", { name: `Panel ${String(panel)}`, exact: true }).click();
    await expect(row("model-field").getByRole("switch").first()).toHaveAttribute("aria-checked", "true");
    const settings = row("model-field").getByRole("button", { name: /settings$/u });
    if (await settings.getAttribute("aria-expanded") === "false") await settings.click();
    const field = await loadedField();
    expect(field.forecast).toEqual(forecast);
    const chip = page.getByTestId(`panel-chip-${String(panel)}`);
    await expect(chip).toHaveText(`${String(panel)} • ${product.label}`, { timeout: FIELD_MS });
    await expect(chip).toHaveAccessibleName(`Panel ${String(panel)}: ${product.label}. Choose parameter.`);
    const scale = page.getByTestId(`map-panel-${String(panel)}`).getByRole("complementary", { name: "Parameter scale" });
    await expect(scale).toHaveAccessibleDescription(`${product.label} · ${product.unit} · Panel ${String(panel)}`);
    await expect(scale.getByRole("button", { name: "Model layer", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(scale.getByRole("img")).toHaveCount(0);
    const scaleValue = await scale.getByRole("slider", { name: "Explore parameter scale" }).getAttribute("aria-valuetext");
    expect(scaleValue).toMatch(/^-?\d+(?:\.\d+)? /u);
    expect(scaleValue).toContain(product.unit);
    panels.push({ panel, ...field, chip: await chip.getAttribute("aria-label"), scale: await scale.getAttribute("aria-description"),
      scaleValue });
  }
  await expect(page.getByLabel("Valid time", { exact: true })).toHaveAttribute("aria-busy", "false");
  await page.getByRole("button", { name: "Close map details", exact: true }).click();
  return panels;
}

/** Compare only map pixels, clear of the chips, scales, rail and timeline. */
export async function weatherPixels(panel: number) {
  const box = await page.getByTestId(`map-panel-${String(panel)}`).boundingBox();
  if (box === null) throw new Error(`no box for panel ${String(panel)}`);
  const clip = { x: box.x + box.width * 0.2, y: box.y + box.height * 0.35, width: box.width * 0.5, height: box.height * 0.35 };
  const toggle = page.getByTestId(`map-panel-${String(panel)}`).getByRole("button", { name: "Model layer", exact: true });
  await page.mouse.move(0, 0);
  await nextFrames(page);
  const shown = await regionStats(page, await page.screenshot({ clip }));
  await toggle.click();
  let hidden: Awaited<ReturnType<typeof regionStats>>;
  try {
    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await page.mouse.move(0, 0);
    await nextFrames(page);
    hidden = await regionStats(page, await page.screenshot({ clip }));
  } finally {
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await nextFrames(page);
  }
  expect(Math.abs(shown.mean - hidden.mean), `panel ${String(panel)} draws weather over its basemap`).toBeGreaterThan(2);
  return { clip, shown, hidden };
}

/** The measure.spec.ts rAF recorder, bounded to an interaction with loaded weather. */
export async function measureFrames(action: () => Promise<void>) {
  await page.evaluate(() => {
    const frames: number[] = [];
    window.__sharppyFrames = frames;
    const tick = (now: number): void => {
      frames.push(now);
      if (window.__sharppyFrames === frames) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  const started = performance.now();
  let frames: number[] = [];
  try {
    await action();
  } finally {
    frames = await page.evaluate(() => {
      const recorded = window.__sharppyFrames ?? [];
      window.__sharppyFrames = undefined;
      return recorded;
    });
  }
  const intervals = frames.slice(1).map((value, index) => value - (frames[index] ?? value)).sort((a, b) => a - b);
  // Debug rAF statistics describe the run; they are not a performance gate.
  expect(intervals.length).toBeGreaterThan(1);
  const rank = (p: number): number => Math.round((intervals[Math.max(0, Math.ceil(p * intervals.length) - 1)] ?? Number.NaN) * 100) / 100;
  return { n: intervals.length, p50: rank(0.5), p95: rank(0.95), min: rank(0), max: rank(1),
    over33ms: intervals.filter((value) => value > 33).length, gestureMs: Math.round(performance.now() - started) };
}

export function setupMapHarness(): void {
  test.skip(CDP === undefined, "SHARPPY_DESKTOP_CDP is not set");
  test.beforeAll(async () => {
    browser = await chromium.connectOverCDP(CDP ?? "");
    const found = browser
      .contexts()
      .flatMap((context) => context.pages())
      .find((candidate) => /^https?:/u.test(candidate.url()));
    if (found === undefined) {
      throw new Error("no page found over CDP");
    }
    page = found;
    page.setDefaultTimeout(30_000);
    await page.setViewportSize({ width: 1920, height: 1080 });
    results.url = page.url();
    results.viewport = page.viewportSize();
    expect(await page.evaluate(() => "__TAURI_INTERNALS__" in window), "real native app").toBe(true);
  });

  test.afterAll(async () => {
    if (OUT !== undefined) {
      writeFileSync(OUT, `${JSON.stringify(results, null, 2)}\n`);
    }
    await browser?.close();
  });

  test.afterEach(async () => {
    const info = test.info();
    if (info.status === info.expectedStatus || info.status === "skipped") return;
    const failure: Record<string, unknown> = { test: info.title, status: info.status, errors: info.errors };
    results.failure = failure;
    try {
      failure.url = page.url();
      failure.text = await page.locator("body").innerText({ timeout: 5_000 });
      failure.accessibility = await page.locator("body").ariaSnapshot({ timeout: 5_000 });
    } catch (error) {
      failure.evidenceError = String(error);
    }
    try {
      await shot("desktop-map-failure.png", true);
    } catch (error) {
      failure.screenshotError = String(error);
    }
    const json = `${JSON.stringify(results, null, 2)}\n`;
    writeFileSync(info.outputPath("desktop-map-failure.json"), json);
    await info.attach("map-failure", { body: json, contentType: "application/json" });
    if (OUT !== undefined) writeFileSync(OUT, json);
  });

}

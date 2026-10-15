// Real-backend forecast map checks, over CDP to the real debug app and through
// the UI only (the fixture test hooks are not in desktop builds): an HRRR
// field frame from sharpmod (newest run first, stepping back while a run is not
// published), its pointer value, a second product, the next hour and a
// prefetched one, SPC outlooks, the radar mosaic, station availability, a
// forecast point, and four panels. Timings and the texts read go to
// SHARPPY_MEASURE_OUT; screenshots to E2E_SCREENSHOT_DIR. Network-dependent:
// run by hand, @map.
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, expect, test, type Browser, type Locator, type Page } from "@playwright/test";

const CDP = process.env.SHARPPY_DESKTOP_CDP;
const OUT = process.env.SHARPPY_MEASURE_OUT;
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
/** The Rust deadlines (field 120 s, outlook and radar 60 s, availability 35 s) plus slack. */
const FIELD_MS = 150_000;
const OVERLAY_MS = 90_000;
const RUN_ATTEMPTS = 3;

const results: Record<string, unknown> = {};
let browser: Browser | undefined;
let page: Page;

async function shot(name: string): Promise<void> {
  if (SHOTS !== undefined) {
    await page.screenshot({ path: join(SHOTS, name) });
  }
}

function seconds(since: number): number {
  return Math.round((Date.now() - since) / 100) / 10;
}

const text = async (locator: Locator): Promise<string> => (await locator.innerText()).replace(/\s+/gu, " ").trim();

function row(id: string): Locator {
  return page.getByTestId(`layer-row-${id}`);
}

async function setLayer(id: string, on: boolean): Promise<void> {
  if (!(await page.getByTestId("layer-panel").isVisible())) {
    await page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true }).click();
  }
  const mapDetail = ["stations", "station-labels", "model-domain", "place-labels", "boundaries"].includes(id);
  await page.getByRole("group", { name: "Layer categories" }).getByRole("button", { name: mapDetail ? "Map details" : "Weather", exact: true }).click();
  const settings = row(id).getByRole("button", { name: /settings$/u });
  if (await settings.count() > 0 && await settings.getAttribute("aria-expanded") === "false") await settings.click();
  const toggle = row(id).getByRole("switch").first();
  if ((await toggle.getAttribute("aria-checked")) !== String(on)) {
    await toggle.click();
  }
  await expect(toggle).toHaveAttribute("aria-checked", String(on));
}

/** Waits for the model field row to show its frame or an error; returns which and how long. */
async function fieldOutcome(): Promise<{ ok: boolean; seconds: number; text: string }> {
  const started = Date.now();
  const shown = row("model-field").getByTestId("field-shown");
  const failed = row("model-field").getByTestId("field-error");
  await expect.poll(async () => (await shown.isVisible()) || (await failed.isVisible()), { timeout: FIELD_MS, intervals: [250] }).toBe(true);
  const ok = await shown.isVisible();
  return { ok, seconds: seconds(started), text: await text(ok ? shown : failed) };
}

/** The map's centre in viewport pixels. */
async function mapCentre(): Promise<{ x: number; y: number }> {
  const box = await page.getByTestId("map").boundingBox();
  if (box === null) {
    throw new Error("no map box");
  }
  return { x: box.x + box.width * 0.45, y: box.y + box.height * 0.55 };
}

test.describe.configure({ mode: "serial" });

test.describe("real-backend forecast map", () => {
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
    results.url = page.url();
  });

  test.afterAll(async () => {
    if (OUT !== undefined) {
      writeFileSync(OUT, `${JSON.stringify(results, null, 2)}\n`);
    }
    await browser?.close();
  });

  test("an HRRR field from sharpmod, its value, another product, and the next hours @map", async () => {
    test.setTimeout(RUN_ATTEMPTS * FIELD_MS + 4 * FIELD_MS);
    test.skip(!(await page.evaluate(() => "__TAURI_INTERNALS__" in window)), "not running inside the Tauri app");
    await page.waitForFunction(() => performance.getEntriesByName("sharppy:stations-rendered").length > 0, undefined, {
      timeout: 60_000,
    });
    await expect(page.getByTestId("catalogue-announcement")).toHaveText("933 stations loaded");
    const toolbar = page.getByRole("group", { name: "Forecast", exact: true });
    const run = toolbar.getByRole("button", { name: "Choose run" });
    await expect(toolbar.getByRole("button", { name: "Choose model" })).toContainText("HRRR");
    results.forecastFields = {
      run: await run.innerText(),
      hours: Number(await page.getByRole("slider", { name: "Forecast hour" }).getAttribute("max")) + 1,
      valid: await text(page.getByLabel("Valid time")),
    };
    await setLayer("model-field", true);
    const attempts: unknown[] = [];
    let outcome = { ok: false, seconds: 0, text: "" };
    for (let attempt = 0; attempt < RUN_ATTEMPTS && !outcome.ok; attempt += 1) {
      if (attempt > 0) {
        // An older run, as the newest may not be published yet.
        await run.click();
        const setup = page.getByRole("dialog", { name: "Model run" });
        const runs = setup.getByRole("button", { name: /^Run /u });
        const states = await runs.evaluateAll((items) => items.map((item) => item.getAttribute("aria-pressed")));
        const chosen = states.indexOf("true");
        expect(chosen).toBeGreaterThanOrEqual(0);
        if (chosen === 0) await setup.getByRole("button", { name: "Previous run date" }).click();
        await runs.nth(chosen === 0 ? states.length - 1 : chosen - 1).click();
      }
      outcome = await fieldOutcome();
      attempts.push({ run: await run.innerText(), ...outcome });
    }
    results.fieldAttempts = attempts;
    expect(outcome.ok, `a field frame loaded: ${JSON.stringify(attempts)}`).toBe(true);
    const scale = page.getByRole("complementary", { name: "Parameter scale" });
    await expect(scale).toContainText("dBZ");
    await expect(page.getByTestId("panel-chip-1")).toHaveCount(0);
    results.scale = await text(scale);
    await shot("desktop-map-field.png");

    await row("model-field").getByRole("combobox", { name: "Product" }).click();
    await page.getByRole("listbox", { name: "Product" }).locator('[value="tmp-2m"]').click();
    results.secondProduct = await fieldOutcome();
    await expect(scale).toContainText("°F");
    results.secondScale = await text(scale);

    const timeline = page.getByRole("region", { name: "Forecast timeline" });
    const next = timeline.getByRole("button", { name: "Next frame" });
    await next.click();
    results.nextHour = await fieldOutcome();
    // The frame after it was prefetched while this one showed.
    await page.waitForTimeout(15_000);
    await next.click();
    results.prefetchedHour = await fieldOutcome();
    results.timeline = await text(timeline);
    await shot("desktop-map-field-next.png");
  });

  test("SPC outlooks, the radar mosaic, and station availability @map", async () => {
    test.setTimeout(4 * OVERLAY_MS);
    await setLayer("model-field", false);
    await setLayer("spc-cat", true);
    const started = Date.now();
    const cat = page.locator('[data-testid^="outlook-"][data-testid$="-cat"]');
    await expect(cat).toBeVisible({ timeout: OVERLAY_MS });
    results.outlookCat = { seconds: seconds(started), text: await text(cat) };
    await setLayer("spc-torn", true);
    const torn = page.locator('[data-testid^="outlook-"][data-testid$="-torn"]');
    await expect(torn).toBeVisible({ timeout: OVERLAY_MS });
    results.outlookTorn = await text(torn);

    await setLayer("radar", true);
    const radarStarted = Date.now();
    const radar = row("radar").locator('[data-testid="radar-shown"], [data-testid="radar-error"]');
    await expect(radar).toBeVisible({ timeout: OVERLAY_MS });
    results.radar = { seconds: seconds(radarStarted), text: await text(radar) };
    await shot("desktop-map-overlays.png");

    await setLayer("stations", true);
    const select = row("stations").getByRole("combobox", { name: "Availability" });
    await select.click();
    const option = page.getByRole("listbox", { name: "Availability" }).getByRole("option").nth(1);
    const release = await option.getAttribute("value");
    await option.click();
    const availabilityStarted = Date.now();
    const status = row("stations").locator('[data-testid="availability-shown"], [data-testid="availability-error"]');
    await expect(status).toBeVisible({ timeout: OVERLAY_MS });
    results.availability = {
      release,
      seconds: seconds(availabilityStarted),
      text: await text(status),
      count: await text(page.getByTestId("catalogue-announcement")),
    };
    for (const id of ["spc-cat", "spc-torn", "radar"]) {
      await setLayer(id, false);
    }
    await setLayer("stations", true);
    await select.click();
    await page.getByRole("option", { name: "Bundled list only", exact: true }).click();
  });

  test("a forecast point, and four synchronized panels @map", async () => {
    test.setTimeout(4 * FIELD_MS);
    await setLayer("model-field", true);
    await fieldOutcome();
    const centre = await mapCentre();
    await page.mouse.click(centre.x, centre.y);
    const inspector = page.getByRole("complementary", { name: "Inspector" });
    await expect(inspector).toContainText("Forecast point");
    results.point = await text(inspector);

    const started = Date.now();
    await page.getByRole("toolbar", { name: "Map" }).getByRole("button", { name: "Four synchronized panels" }).click();
    const chips = [1, 2, 3, 4].map((n) => page.getByTestId(`panel-chip-${String(n)}`));
    await expect
      .poll(async () => (await Promise.all(chips.map((chip) => text(chip)))).every((t) => /valid|could not/u.test(t)), {
        timeout: FIELD_MS,
        intervals: [500],
      })
      .toBe(true);
    results.fourPanels = { seconds: seconds(started), chips: await Promise.all(chips.map((chip) => text(chip))) };
    await shot("desktop-map-four-panels.png");
    await page.getByRole("toolbar", { name: "Map" }).getByRole("button", { name: "One map" }).click();
    await page.getByRole("button", { name: "Clear selection" }).click();
  });
});

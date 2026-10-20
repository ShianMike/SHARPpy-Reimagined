// Real-backend sounding checks, over CDP to the real debug app and through the
// UI only: an observed sounding at 72357 (the form's latest 00Z or 12Z
// release, stepping back one release at a time while no provider has it yet),
// a model sounding at the station, and the example BUFKIT file. Every image
// is sharpmod's renderer output. Timings and the values read go to
// SHARPPY_MEASURE_OUT; screenshots to E2E_SCREENSHOT_DIR. Network-dependent:
// run by hand, @soundings.
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, expect, test, type Browser, type Locator, type Page } from "@playwright/test";

const CDP = process.env.SHARPPY_DESKTOP_CDP;
const OUT = process.env.SHARPPY_MEASURE_OUT;
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const BUFKIT = join("..", "examples", "soundings", "hrrr_kbvo_20260625_06z.buf");
/** Releases tried before the observed check gives up: the latest and two older ones. */
const OBSERVED_ATTEMPTS = 3;
/** The Rust timeouts (observed 90 s, model 300 s, file 60 s, render 200 s) plus slack. */
const OPEN_OBSERVED_MS = 120_000;
const OPEN_MODEL_MS = 330_000;
const OPEN_FILE_MS = 90_000;
const RENDER_MS = 230_000;

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

function railItem(name: string): Locator {
  return page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name });
}

async function showWorkspace(name: string): Promise<void> {
  await railItem(name).click();
  await expect(railItem(name)).toHaveAttribute("aria-current", "page");
}

function viewerStage(): Locator {
  return page.getByTestId("sounding-viewer").getByTestId("sounding-stage");
}

/**
 * Clicks an Open sounding button and waits for the new sounding to join the
 * open list, or for the form's error notice.
 */
async function open(button: Locator, error: Locator, timeout: number): Promise<{ ok: boolean; seconds: number; text: string }> {
  const items = page.getByTestId("open-soundings").getByRole("listitem");
  const before = await items.count();
  // An edit clears an earlier attempt's notice, so any notice seen below is this attempt's.
  await expect(error).toBeHidden();
  await expect(button).toBeEnabled();
  const started = Date.now();
  await button.click();
  await expect
    .poll(async () => (await error.isVisible()) || (await items.count()) > before, { timeout, intervals: [250] })
    .toBe(true);
  const took = seconds(started);
  if (await error.isVisible()) {
    return { ok: false, seconds: took, text: (await error.innerText()).replace(/\s+/gu, " ") };
  }
  return { ok: true, seconds: took, text: await page.getByTestId("sounding-title").innerText() };
}

/** Waits for the stage's image (or its error), decoded; returns the wait and the PNG's size. */
async function drawn(stage: Locator, previousSrc: string | null = null): Promise<{ seconds: number; width: number; height: number }> {
  const started = Date.now();
  const image = stage.getByTestId("sounding-image");
  const failure = stage.getByTestId("render-error");
  await expect
    .poll(
      async () => {
        if (await failure.isVisible()) {
          return `error: ${(await failure.innerText()).replace(/\s+/gu, " ")}`;
        }
        if ((await image.count()) === 0) {
          return "drawing";
        }
        return image.evaluate((element, previous) => {
          const img = element as HTMLImageElement;
          return img.complete && img.naturalWidth > 0 && img.src !== previous ? "drawn" : "decoding";
        }, previousSrc);
      },
      { timeout: RENDER_MS, intervals: [250] },
    )
    .toBe("drawn");
  const size = await image.evaluate((element) => {
    const img = element as HTMLImageElement;
    return { width: img.naturalWidth, height: img.naturalHeight };
  });
  return { seconds: seconds(started), ...size };
}

/** The release before a date and 00Z or 12Z launch hour. */
function previousRelease(date: string, hour: string): { date: string; hour: string } {
  if (hour === "12") {
    return { date, hour: "0" };
  }
  const day = new Date(`${date}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() - 1);
  return { date: day.toISOString().slice(0, 10), hour: "12" };
}

test.describe.configure({ mode: "serial" });

test.describe("real-backend soundings", () => {
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

  test("an observed sounding at 72357, drawn by sharpmod @soundings", async () => {
    test.setTimeout(OBSERVED_ATTEMPTS * OPEN_OBSERVED_MS + 2 * RENDER_MS + 60_000);
    test.skip(!(await page.evaluate(() => "__TAURI_INTERNALS__" in window)), "not running inside the Tauri app");
    await page.waitForFunction(() => performance.getEntriesByName("sharppy:stations-rendered").length > 0, undefined, {
      timeout: 60_000,
    });
    await expect(page.getByTestId("catalogue-announcement")).toHaveText(/^[1-9]\d* stations loaded$/u);
    await page.getByRole("button", { name: "Search stations" }).click();
    const search = page.getByRole("combobox", { name: "Search stations" });
    await search.click();
    await search.fill("72357");
    await page.keyboard.press("Enter");
    const inspector = page.getByRole("complementary", { name: "Inspector" });
    await expect(inspector).toContainText("72357");
    await inspector.getByRole("button", { name: "Open sounding" }).click();
    await expect(railItem("Soundings")).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("textbox", { name: "Station" })).toHaveValue("72357");

    const date = page.getByRole("button", { name: "Date (UTC)" });
    const launch = page.getByRole("combobox", { name: "Launch" });
    const attempts: unknown[] = [];
    let outcome = { ok: false, seconds: 0, text: "" };
    for (let attempt = 0; attempt < OBSERVED_ATTEMPTS && !outcome.ok; attempt += 1) {
      if (attempt > 0) {
        const previous = previousRelease(await date.getAttribute("value") ?? "", await launch.getAttribute("value") ?? "0");
        await date.click();
        await page.getByRole("button", { name: "Choose month and year" }).click();
        await page.getByRole("spinbutton", { name: "Calendar year" }).fill(previous.date.slice(0, 4));
        const moment = new Date(`${previous.date}T00:00:00Z`);
        await page.getByRole("button", { name: new Intl.DateTimeFormat("en", { month: "short", timeZone: "UTC" }).format(moment), exact: true }).click();
        await page.getByRole("button", { name: new Intl.DateTimeFormat("en", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(moment), exact: true }).click();
        await launch.click();
        await page.getByRole("option", { name: `${previous.hour.padStart(2, "0")}Z`, exact: true }).click();
      }
      const time = `${await date.getAttribute("value") ?? ""} ${(await launch.getAttribute("value") ?? "0").padStart(2, "0")}Z`;
      outcome = await open(page.getByTestId("open-observed"), page.getByTestId("observed-error"), OPEN_OBSERVED_MS);
      attempts.push({ time, ...outcome });
    }
    results.observedAttempts = attempts;
    expect(outcome.ok, `an observed sounding opened: ${JSON.stringify(attempts)}`).toBe(true);
    expect(outcome.text).toContain("72357");

    const first = await drawn(viewerStage());
    const meta = await page.getByTestId("sounding-viewer").locator("header").innerText();
    results.observed = { title: outcome.text, header: meta.replace(/\s+/gu, " "), firstRender: first };
    expect(first.width).toBeGreaterThan(1000);
    await shot("desktop-sounding-observed.png");

    await page.getByText("Chart panels", { exact: true }).filter({ visible: true }).click();
    const parcel = page.getByRole("group", { name: "Parcel" });
    const before = await viewerStage().getByTestId("sounding-image").getAttribute("src");
    await parcel.getByRole("button", { name: "ML" }).click();
    results.observedRedrawMl = await drawn(viewerStage(), before);

  });

  test("a model sounding at the selected station @soundings", async () => {
    test.setTimeout(2 * OPEN_MODEL_MS + RENDER_MS + 60_000);
    test.skip(!(await page.evaluate(() => "__TAURI_INTERNALS__" in window)), "not running inside the Tauri app");
    await showWorkspace("Soundings");
    await page.getByRole("tab", { name: "Model" }).click();
    const form = page.getByRole("form", { name: "Model sounding" });
    const model = form.getByRole("combobox", { name: "Model" });
    await expect(model).toBeVisible({ timeout: 60_000 });
    await model.click();
    await page.getByRole("option", { name: "HRRR", exact: true }).click();
    await expect(form.getByRole("switch", { name: "At station 72357" })).toBeChecked();
    const run = form.getByRole("combobox", { name: "Run" });
    const attempts: unknown[] = [];
    let outcome = { ok: false, seconds: 0, text: "" };
    // The suggested run first; if it is not published yet, the run before it.
    for (let attempt = 0; attempt < 2 && !outcome.ok; attempt += 1) {
      if (attempt > 0) {
        await run.click();
        const options = page.getByRole("listbox", { name: "Run" }).getByRole("option");
        const runs = await options.evaluateAll((items) => items.map((item) => item.getAttribute("value")));
        const index = runs.indexOf(await run.getAttribute("value"));
        if (index < 0 || index + 1 >= runs.length) {
          await page.keyboard.press("Escape");
          break;
        }
        await options.nth(index + 1).click();
      }
      const chosen = {
        run: await run.getAttribute("value"),
        forecast: await form.getByRole("combobox", { name: "Forecast" }).getAttribute("value"),
      };
      outcome = await open(page.getByTestId("open-model"), page.getByTestId("model-error"), OPEN_MODEL_MS);
      attempts.push({ ...chosen, ...outcome });
    }
    results.modelAttempts = attempts;
    expect(outcome.ok, `a model sounding opened: ${JSON.stringify(attempts)}`).toBe(true);
    const image = await drawn(viewerStage());
    const meta = await page.getByTestId("sounding-viewer").locator("header").innerText();
    results.model = { title: outcome.text, header: meta.replace(/\s+/gu, " "), firstRender: image };
    await shot("desktop-sounding-model.png");
  });

  test("the example BUFKIT file, its last time and return to the first @soundings", async () => {
    test.setTimeout(OPEN_FILE_MS + 3 * RENDER_MS);
    test.skip(!(await page.evaluate(() => "__TAURI_INTERNALS__" in window)), "not running inside the Tauri app");
    await showWorkspace("Soundings");
    await page.getByRole("tab", { name: "File" }).click();
    await page.locator('input[type="file"]').setInputFiles(BUFKIT);
    const outcome = await open(page.getByTestId("open-file"), page.getByTestId("file-error"), OPEN_FILE_MS);
    expect(outcome.ok, outcome.text).toBe(true);
    const image = await drawn(viewerStage());
    const meta = await page.getByTestId("sounding-viewer").locator("header").innerText();
    expect(meta).toContain("time 1 of 49");
    const time = page.getByRole("combobox", { name: "Sounding time (UTC)" });
    const firstSrc = await viewerStage().getByTestId("sounding-image").getAttribute("src");
    await time.click();
    await expect(page.getByRole("listbox", { name: "Sounding time (UTC)" }).getByRole("option")).toHaveCount(49);
    await page.keyboard.press("End");
    await page.keyboard.press("Enter");
    await expect(time).toHaveAttribute("value", "48");
    const lastRender = await drawn(viewerStage(), firstSrc);
    const lastHeader = await page.getByTestId("sounding-viewer").locator("header").innerText();
    expect(lastHeader).toContain("File, 2026-06-27 06Z, Selected profile, time 49 of 49");
    const lastSrc = await viewerStage().getByTestId("sounding-image").getAttribute("src");
    await time.click();
    await page.keyboard.press("Home");
    await page.keyboard.press("Enter");
    await expect(time).toHaveAttribute("value", "0");
    const restoredRender = await drawn(viewerStage(), lastSrc);
    expect(await page.getByTestId("sounding-viewer").locator("header").innerText()).toContain("File, 2026-06-25 06Z, 50 levels, time 1 of 49");
    results.file = { open: outcome, header: meta.replace(/\s+/gu, " "), firstRender: image, lastHeader: lastHeader.replace(/\s+/gu, " "), lastRender, restoredRender };
    await shot("desktop-sounding-file.png");

  });
});

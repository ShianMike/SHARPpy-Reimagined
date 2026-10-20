// Desktop measurement harness (DESIGN §4.4). It connects over CDP to a page
// that is already running: the real debug app (WebView2), or the browser build
// in Edge for the frame-time comparison. It is a measurement harness, not app
// code, so it calls window.__TAURI_INTERNALS__.invoke directly.
//
// - @load: the start-up marks, "Backend ready", the station count, screenshots,
//   the catalogue payload size, IPC round trips, and a reload with a CSP
//   violation listener. Runs only inside the Tauri app.
// - @frames: requestAnimationFrame intervals during a scripted 600 px drag
//   over 1 s and 10 wheel steps in and out, plus the effective profile. Runs in
//   any page.
// - @python-missing: the app started with SHARPPY_PYTHON pointing nowhere.
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, expect, test, type Browser, type Page } from "@playwright/test";
import appManifest from "../../package.json" with { type: "json" };

declare global {
  interface Window {
    __TAURI_INTERNALS__?: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
    __sharppyFrames?: number[] | undefined;
    __sharppyCsp?: string[];
  }
}

const CDP = process.env.SHARPPY_DESKTOP_CDP;
const OUT = process.env.SHARPPY_MEASURE_OUT;
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const MARKS = [
  "sharppy:shell-painted",
  "sharppy:map-loaded",
  "sharppy:basemap-loaded",
  "sharppy:catalog-requested",
  "sharppy:catalog-received",
  "sharppy:stations-rendered",
] as const;

interface Stats {
  n: number;
  p50: number;
  p95: number;
  min: number;
  max: number;
}

const results: Record<string, unknown> = {};
let browser: Browser | undefined;
let page: Page;

function stats(values: readonly number[]): Stats {
  const sorted = [...values].sort((a, b) => a - b);
  const rank = (p: number): number => sorted[Math.max(0, Math.ceil((p / 100) * sorted.length) - 1)] ?? Number.NaN;
  const round = (v: number): number => Math.round(v * 100) / 100;
  return {
    n: sorted.length,
    p50: round(rank(50)),
    p95: round(rank(95)),
    min: round(sorted[0] ?? Number.NaN),
    max: round(sorted[sorted.length - 1] ?? Number.NaN),
  };
}

async function shot(name: string): Promise<void> {
  if (SHOTS !== undefined) {
    await page.screenshot({ path: join(SHOTS, name) });
  }
}

async function isTauri(): Promise<boolean> {
  return page.evaluate(() => window.__TAURI_INTERNALS__ !== undefined);
}

async function waitForMarks(names: readonly string[]): Promise<void> {
  await page.waitForFunction((wanted) => wanted.every((n) => performance.getEntriesByName(n).length > 0), names, {
    timeout: 60_000,
  });
}

test.describe.configure({ mode: "serial" });

test.describe("desktop measurements", () => {
  test.skip(CDP === undefined, "SHARPPY_DESKTOP_CDP is not set");

  test.beforeAll(async () => {
    browser = await chromium.connectOverCDP(CDP ?? "");
    const found = browser
      .contexts()
      .flatMap((context) => context.pages())
      .find((candidate) => /^https?:/u.test(candidate.url())); // skips devtools:, edge:, about: pages
    if (found === undefined) {
      throw new Error("no page found over CDP");
    }
    page = found;
    await page.setViewportSize({ width: 1920, height: 1080 });
    results.url = page.url();
  });

  test.afterAll(async () => {
    if (OUT !== undefined) {
      writeFileSync(OUT, `${JSON.stringify(results, null, 2)}\n`);
    }
    await browser?.close();
  });

  test("start-up marks, backend ready, and loaded stations @load", async () => {
    test.skip(!(await isTauri()), "not running inside the Tauri app");
    await waitForMarks(["sharppy:stations-rendered", "sharppy:basemap-loaded"]);
    const timing = await page.evaluate((names) => {
      const marks: Record<string, number> = {};
      for (const name of names) {
        const entry = performance.getEntriesByName(name)[0];
        if (entry !== undefined) {
          marks[name] = entry.startTime;
        }
      }
      return { timeOrigin: performance.timeOrigin, marks };
    }, MARKS);
    const rendered = timing.marks["sharppy:stations-rendered"] ?? Number.NaN;
    const requested = timing.marks["sharppy:catalog-requested"] ?? Number.NaN;
    const received = timing.marks["sharppy:catalog-received"] ?? Number.NaN;
    results.timeOrigin = timing.timeOrigin;
    results.marks = timing.marks;
    results.stationsRenderedEpochMs = timing.timeOrigin + rendered;
    results.firstCatalogLoadMs = Math.round((received - requested) * 100) / 100;
    await expect(page.getByTestId("catalogue-announcement")).toHaveText(/^[1-9]\d* stations loaded$/u);
    await shot("desktop-content.png");
  });

  test("health popover shows the Python and sharpmod versions @load", async () => {
    test.skip(!(await isTauri()), "not running inside the Tauri app");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Logs" }).click();
    await page.getByTestId("health-indicator").click();
    const popover = page.getByRole("dialog", { name: "Backend status" });
    await expect(popover).toContainText("Backend ready");
    await expect(popover).toContainText(/3\.(?:11|12|13)\.\d+/u);
    await expect(popover).toContainText(appManifest.version);
    results.healthPopover = await popover.innerText();
    await shot("desktop-health.png");
    await page.keyboard.press("Escape");
    await expect(popover).toHaveCount(0);
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer" }).click();
  });

  test("a real station is selected through search @load", async () => {
    test.skip(!(await isTauri()), "not running inside the Tauri app");
    await page.getByRole("button", { name: "Search stations" }).click();
    const input = page.getByRole("combobox", { name: "Search stations" });
    await input.click();
    await input.fill("72357");
    await page.keyboard.press("Enter");
    const inspector = page.getByRole("complementary", { name: "Inspector" });
    await expect(inspector).toContainText("72357");
    await expect(inspector).toContainText("Norman/Max Westheimer A, Ok., Usa");
    await page.waitForTimeout(1_500); // let the 600 ms camera move settle
    await shot("desktop-stations.png");
    await inspector.getByRole("button", { name: "Clear selection" }).first().click();
    await expect(inspector).toBeHidden();
  });

  test("catalogue payload and IPC round trips @load", async () => {
    test.skip(!(await isTauri()), "not running inside the Tauri app");
    const ipc = await page.evaluate(async () => {
      const internals = window.__TAURI_INTERNALS__;
      if (internals === undefined) {
        throw new Error("no Tauri internals");
      }
      const payload = JSON.stringify(await internals.invoke("get_station_catalog")).length;
      const time = async (command: string, count: number): Promise<number[]> => {
        const out: number[] = [];
        for (let i = 0; i < count; i += 1) {
          const start = performance.now();
          await internals.invoke(command);
          out.push(performance.now() - start);
        }
        return out;
      };
      const health = await time("get_backend_health", 50);
      const catalog = await time("get_station_catalog", 20);
      return { payload, health, catalog };
    });
    results.catalogPayloadBytes = ipc.payload;
    results.ipcHealthMs = stats(ipc.health);
    results.ipcCatalogCachedMs = stats(ipc.catalog);
    expect(ipc.payload).toBeGreaterThan(100_000);
  });

  test("a full reload records no CSP violation @load", async () => {
    test.skip(!(await isTauri()), "not running inside the Tauri app");
    await page.context().addInitScript(() => {
      window.__sharppyCsp = [];
      document.addEventListener("securitypolicyviolation", (event) => {
        window.__sharppyCsp?.push(`${event.violatedDirective} ${event.blockedURI}`);
      });
    });
    const consoleCsp: string[] = [];
    page.on("console", (message) => {
      if (/content security policy/iu.test(message.text())) {
        consoleCsp.push(message.text());
      }
    });
    await page.reload();
    await waitForMarks(["sharppy:stations-rendered", "sharppy:basemap-loaded"]);
    await expect(page.getByTestId("catalogue-announcement")).toHaveText(/^[1-9]\d* stations loaded$/u);
    const violations = await page.evaluate(() => window.__sharppyCsp ?? ["listener missing"]);
    results.csp = { violations, consoleMessages: consoleCsp };
    expect(violations).toEqual([]);
    expect(consoleCsp).toEqual([]);
  });

  test("frame times during a scripted pan and zoom @frames", async () => {
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMarks(["sharppy:stations-rendered"]);
    const map = page.getByTestId("map");
    const box = await map.boundingBox();
    if (box === null) {
      throw new Error("the map has no box");
    }
    const centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    await page.evaluate(() => {
      const frames: number[] = [];
      window.__sharppyFrames = frames;
      const tick = (now: number): void => {
        frames.push(now);
        if (window.__sharppyFrames === frames) {
          requestAnimationFrame(tick);
        }
      };
      requestAnimationFrame(tick);
    });
    const start = performance.now();
    await page.mouse.move(centre.x - 300, centre.y);
    await page.mouse.down();
    for (let step = 1; step <= 60; step += 1) {
      await page.mouse.move(centre.x - 300 + step * 10, centre.y);
      await page.waitForTimeout(16);
    }
    await page.mouse.up();
    await page.waitForTimeout(300);
    for (const delta of [...Array<number>(10).fill(-120), ...Array<number>(10).fill(120)]) {
      await page.mouse.wheel(0, delta);
      await page.waitForTimeout(100);
    }
    await page.waitForTimeout(500);
    const gestureMs = performance.now() - start;
    const frames = await page.evaluate(() => {
      const recorded = window.__sharppyFrames ?? [];
      window.__sharppyFrames = undefined;
      return recorded;
    });
    const intervals = frames.slice(1).map((t, i) => t - (frames[i] ?? t));
    results.frames = {
      ...stats(intervals),
      over33ms: intervals.filter((v) => v > 33).length,
      gestureMs: Math.round(gestureMs),
    };
    await page.getByRole("button", { name: "Reset view" }).click();
    const rail = page.getByRole("navigation", { name: "Workspaces" });
    await rail.getByRole("button", { name: "Settings" }).click();
    await page.getByRole("button", { name: "Map rendering", exact: true }).click();
    const effective = await page.getByTestId("effective-profile").innerText();
    const reasons = await page.getByRole("list", { name: "Reasons" }).getByRole("listitem").allInnerTexts();
    await page.getByRole("button", { name: "Device Info", exact: true }).click();
    results.profile = {
      attribute: await page.evaluate(() => document.documentElement.dataset.graphicsProfile ?? null),
      effective,
      reasons,
      capabilities: await page.getByTestId("capability-table").innerText(),
    };
    await rail.getByRole("button", { name: "Model Viewer" }).click();
    expect(intervals.length).toBeGreaterThan(30);
  });

  test("without Python the basemap draws and the instruction shows @python-missing", async () => {
    test.skip(!(await isTauri()), "not running inside the Tauri app");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMarks(["sharppy:basemap-loaded"]);
    await expect(page.getByText("Stations could not be loaded")).toBeVisible();
    await expect(page.getByText(/SHARPPY_PYTHON/u).first()).toBeVisible();
    await expect(page.getByTestId("catalogue-announcement")).toHaveCount(0);
    results.pythonMissing = { notice: await page.getByText("Stations could not be loaded").locator("..").innerText() };
    await shot("desktop-python-missing.png");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Logs" }).click();
    await expect(page.getByTestId("health-indicator")).toHaveText("Backend unavailable");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer" }).click();
  });
});

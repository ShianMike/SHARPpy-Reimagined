import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { availableParallelism, cpus, platform, release } from "node:os";
import { dirname } from "node:path";
import { expect, test } from "./support/test";
import { waitForMapDrawn } from "./support/app";
import { layerSettings, turnOn } from "./support/ui";
import type { GifBenchmarkCapture } from "../src/features/forecastExport/gifExportBenchmark";

const output = process.env.SHARPPY_GIF_BENCHMARK_OUT;
const width = 1920;
const height = 1080;
const delay = 750;
const hours = [0, 1, 2];

test("records complete forecast GIF export phases and verifies the animation", async ({ browser, page }) => {
  test.skip(output === undefined, "manual benchmark; set SHARPPY_GIF_BENCHMARK_OUT");
  test.setTimeout(600_000);
  const repeat = Number(process.env.SHARPPY_GIF_BENCHMARK_REPEATS ?? 5);
  if (output === undefined || !Number.isInteger(repeat) || repeat < 2 || repeat > 30) throw new Error("Choose 2–30 repeats and a new output path");
  const fixturePaths = ["src/lib/backend/fixtureTransport.ts", "dist-fixture/geo/states.geojson",
    "dist-fixture/geo/countries.geojson", "dist-fixture/geo/coastline.geojson", "dist-fixture/geo/lakes.geojson",
    "../src-tauri/contracts/public/map/field.shrf"];
  const fixtureHashes = Object.fromEntries(fixturePaths.map((path) => [path, createHash("sha256").update(readFileSync(path)).digest("hex")]));
  await page.addInitScript(() => {
    window.__SHARPPY_GIF_BENCHMARK__ = {
      capture: null,
      blob: null,
      start(opts) {
        this.capture = {
          startedAtMs: performance.now(), finishedAtMs: null, events: [], error: null,
          options: { model: opts.context.model.key, product: opts.product.key, run: opts.run,
            hours: [...opts.hours], width: opts.width, height: opts.height, delay: opts.delay },
        };
        this.blob = null;
      },
      progress(value) { this.capture?.events.push({ ...value, atMs: performance.now() }); },
      finish(blob) { if (this.capture !== null) this.capture.finishedAtMs = performance.now(); this.blob = blob; },
      fail(error) { if (this.capture !== null) this.capture.error = error instanceof Error ? error.message : String(error); },
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  await turnOn(page, "model-field");
  await (await layerSettings(page, "model-field")).getByText("Details & source", { exact: true }).click();
  await expect(page.getByTestId("field-shown")).toBeVisible();
  await page.getByRole("button", { name: /Export forecast/u }).click();
  const dialog = page.getByRole("dialog", { name: /Export forecast/u });
  await dialog.getByRole("button", { name: "Clear", exact: true }).click();
  for (const hour of hours) await dialog.getByRole("checkbox", { name: `Export forecast hour ${String(hour)}`, exact: true }).check();
  const resolution = dialog.getByRole("combobox", { name: /resolution/u });
  if (await resolution.count() > 0 && !(await resolution.innerText()).includes("1920")) {
    await resolution.click();
    await page.getByRole("option", { name: /1920/u }).click();
  }
  const samples = [];
  for (let index = 0; index < repeat; index++) {
    const create = dialog.getByRole("button", { name: /^(?:Create GIF|Create again)$/u });
    await expect(create).toBeEnabled();
    // Invoke the same UI action without timing Playwright's post-click waits,
    // which can be delayed by software WebGL redraws.
    await create.dispatchEvent("click");
    await page.waitForFunction(() => {
      const hook = window.__SHARPPY_GIF_BENCHMARK__;
      return hook?.blob != null || hook?.capture?.error != null;
    }, undefined, { timeout: 120_000, polling: 250 });
    const result = await page.evaluate(async () => {
      const hook = window.__SHARPPY_GIF_BENCHMARK__;
      if (hook?.capture?.error != null) throw new Error(hook.capture.error);
      if (hook?.blob == null || hook.capture === null) throw new Error("The export did not produce a complete benchmark record.");
      const blob = hook.blob;
      const raw = await blob.arrayBuffer();
      const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", raw))]
        .map((byte) => byte.toString(16).padStart(2, "0")).join("");
      // Chromium's decoder is independent of gifenc. Decode every actual frame
      // after timing so a valid-looking header cannot pass on its own.
      const decoder = new ImageDecoder({ type: "image/gif", data: raw });
      try {
        await decoder.tracks.ready;
        const track = decoder.tracks.selectedTrack;
        if (track === null) throw new Error("The GIF decoder did not find an animation.");
        const frames = [];
        for (let n = 0; n < track.frameCount; n++) {
          const { image, complete } = await decoder.decode({ frameIndex: n });
          frames.push({ complete, width: image.displayWidth, height: image.displayHeight, delay: (image.duration ?? 0) / 1000 });
          image.close();
        }
        return { capture: hook.capture, bytes: blob.size, sha256: digest, loop: track.repetitionCount === Infinity, frames };
      } finally { decoder.close(); }
    });
    expect(result.capture.options).toMatchObject({ hours, width, height, delay });
    expect(result.frames).toEqual(hours.map(() => ({ complete: true, width, height, delay })));
    expect(result.loop).toBe(true);
    expect(result.bytes).toBeGreaterThan(0);
    const capture: GifBenchmarkCapture = result.capture;
    const loading = capture.events.find((e) => e.phase === "loading" && e.done === 0);
    const capturing = capture.events.find((e) => e.phase === "capturing" && e.done === 0);
    const encoding = capture.events.find((e) => e.phase === "encoding" && e.done === 0);
    if (loading === undefined || capturing === undefined || encoding === undefined || capture.finishedAtMs === null)
      throw new Error("The export did not record all three phases.");
    expect(capture.events.filter((e) => e.phase === "loading" && e.done > 0).map((e) => e.hour).sort()).toEqual(hours);
    expect(capture.events.filter((e) => e.phase === "encoding" && e.done > 0).map((e) => e.hour)).toEqual(hours);
    expect(capture.finishedAtMs).toBeGreaterThan(encoding.atMs);
    const finished = capture.finishedAtMs;
    samples.push({ index, state: index === 0 ? "first export" : "same-page warm export", ...result,
      milliseconds: { load: capturing.atMs - loading.atMs, capture: encoding.atMs - capturing.atMs,
        encode: finished - encoding.atMs, total: finished - capture.startedAtMs } });
    await expect(dialog.getByText(/^GIF ready/u)).toBeVisible();
  }
  const git = (...args: string[]) => execFileSync("git", args, { encoding: "utf8" }).trim();
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify({ schema_version: 1, workload: "forecast-gif-export",
    measured_at: new Date().toISOString(), source: { revision: git("rev-parse", "HEAD"), tree: git("rev-parse", "HEAD^{tree}"),
      dirty: git("status", "--porcelain") !== "", status: git("status", "--porcelain").split("\n").filter(Boolean) },
    environment: { node: process.version, browser: browser.version(), os: platform(), release: release(),
      cpu: cpus()[0]?.model, availableParallelism: availableParallelism(), swiftshader: process.env.E2E_SWIFTSHADER === "1" },
    fixture_sha256: fixtureHashes, scope: "Production capture, composition and worker GIF encoding in the offline browser fixture. Does not measure provider latency or installed WebView2 performance. First export starts after the initial forecast frame is displayed; warm exports reuse that page's caches. Decode validation and file hashing are outside timing.", samples }, null, 2)}\n`, { flag: "wx" });
});

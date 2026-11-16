// Browser end-to-end tests (DESIGN-tooling T3): the installed Microsoft Edge
// (nothing is downloaded) against `vite preview` of the fixture build, which
// serves the app under the same CSP as the desktop app.
//
// Environment, set in the verification shell only:
// - E2E_SCREENSHOT_DIR: where the specs save their screenshots;
// - E2E_SWIFTSHADER=1: start Edge with --enable-unsafe-swiftshader, for hosts
//   where headless WebGL does not start. Headed runs use the --headed flag.
import { defineConfig } from "@playwright/test";

const swiftshader = process.env.E2E_SWIFTSHADER === "1";
const gifBenchmark = process.env.SHARPPY_GIF_BENCHMARK_OUT !== undefined;
const benchmarkChromium = gifBenchmark ? process.env.SHARPPY_GIF_BENCHMARK_CHROMIUM : undefined;

export default defineConfig({
  testDir: "e2e",
  // The desktop harness connects to the real app; playwright.desktop.config.ts runs it.
  // Anchored at e2e/: a bare "desktop/**" also matches a Desktop folder in the checkout path.
  testIgnore: ["**/e2e/desktop/**", ...(gifBenchmark ? [] : ["**/e2e/forecast-export-benchmark.spec.ts"])],
  outputDir: "test-results",
  // Balance individual tests across CI machines; one browser per machine avoids GPU contention.
  fullyParallel: true,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: "http://127.0.0.1:4173",
    ...(benchmarkChromium === undefined ? { channel: "msedge" } : {}),
    headless: true,
    // Keep DOM/network diagnostics without continuous GPU readbacks during map animation.
    trace: gifBenchmark ? "off" : { mode: "retain-on-failure", screenshots: false },
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
    launchOptions: { args: swiftshader ? ["--enable-unsafe-swiftshader"] : [],
      ...(benchmarkChromium === undefined ? {} : { executablePath: benchmarkChromium }) },
  },
  webServer: {
    command: "npm run preview:fixture",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});

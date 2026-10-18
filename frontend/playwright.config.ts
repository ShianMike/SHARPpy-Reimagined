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

export default defineConfig({
  testDir: "e2e",
  // The desktop harness connects to the real app; playwright.desktop.config.ts runs it.
  // Anchored at e2e/: a bare "desktop/**" also matches a Desktop folder in the checkout path.
  testIgnore: ["**/e2e/desktop/**"],
  outputDir: "test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: "http://127.0.0.1:4173",
    channel: "msedge",
    headless: true,
    // Keep DOM/network diagnostics without continuous GPU readbacks during map animation.
    trace: { mode: "retain-on-failure", screenshots: false },
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
    launchOptions: { args: swiftshader ? ["--enable-unsafe-swiftshader"] : [] },
  },
  webServer: {
    command: "npm run preview:fixture",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});

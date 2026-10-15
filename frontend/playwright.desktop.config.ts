// Desktop measurements (DESIGN §4.4, DESIGN-tooling T3): no web server; the
// spec connects over CDP to the real debug app, which the verification shell
// starts with WebView2 remote debugging enabled.
//
// Environment, set in the verification shell only:
// - SHARPPY_DESKTOP_CDP: the CDP endpoint, for example http://127.0.0.1:9222;
//   without it every test is skipped;
// - SHARPPY_MEASURE_OUT: where the measurements JSON is written;
// - E2E_SCREENSHOT_DIR: where the screenshots are saved.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e/desktop",
  testMatch: "*.spec.ts",
  outputDir: "test-results/desktop",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 120_000,
  expect: { timeout: 30_000 },
});

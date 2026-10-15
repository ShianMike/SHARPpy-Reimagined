// Screenshots go to E2E_SCREENSHOT_DIR (the verification run points it at the
// task's screenshots folder), else to test-results/screenshots.
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";

export function screenshotPath(name: string): string {
  const dir = process.env.E2E_SCREENSHOT_DIR ?? join("test-results", "screenshots");
  mkdirSync(dir, { recursive: true });
  return join(dir, name);
}

export async function saveScreenshot(page: Page, name: string): Promise<Buffer> {
  return page.screenshot({ path: screenshotPath(name), fullPage: false });
}

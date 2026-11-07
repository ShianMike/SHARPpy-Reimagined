import type { Locator, Page } from "@playwright/test";
import appManifest from "../package.json" with { type: "json" };
import releaseNotes from "../release-notes.json" with { type: "json" };
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { boxOf, isInside, viewportBox } from "./support/ui";

const { version } = appManifest;
const release = releaseNotes[version as keyof typeof releaseNotes];

async function expectHighlightsFit(notes: Locator, page: Page): Promise<void> {
  const highlights = notes.getByRole("listitem");
  await expect(highlights).toHaveCount(release.highlights.length);
  await notes.evaluate(async (panel) => { await Promise.allSettled(panel.getAnimations().map((animation) => animation.finished)); });
  const box = await boxOf(notes);
  expect(isInside(box, viewportBox(page))).toBe(true);
  expect(box.width).toBeGreaterThan(box.height);
  const first = await boxOf(highlights.first());
  expect(isInside(first, box)).toBe(true);
  if (release.highlights.length > 1) {
    const second = await boxOf(highlights.nth(1));
    expect(Math.abs(first.y - second.y)).toBeLessThan(1);
    expect(second.x).toBeGreaterThanOrEqual(first.x + first.width);
  }
  expect(await notes.evaluate((element) => {
    const body = element.querySelector("ul")?.parentElement;
    return body !== null && body !== undefined && [element, body].every((part) =>
      part.scrollHeight <= part.clientHeight + 1 && part.scrollWidth <= part.clientWidth + 1);
  })).toBe(true);
  const dismiss = notes.getByRole("button", { name: "Got it", exact: true });
  await expect(dismiss).toBeVisible();
  expect(isInside(await boxOf(dismiss), box)).toBe(true);
}

test("release highlights open from Updates and fit the desktop window", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/");
  const notes = page.getByRole("dialog", { name: `What’s new in ${version}` });
  await expect(notes).toHaveCount(0);
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Updates", exact: true }).click();
  await expect(page.getByRole("heading", { name: `What’s new in ${version}` })).toBeVisible();
  await expect(page.getByText(release.summary)).toBeVisible();
  await saveScreenshot(page, "updates-1920x1080-dark.png");
  const trigger = page.getByRole("button", { name: "View highlights", exact: true });
  await trigger.click();
  await expect(notes).toBeFocused();
  await expect(notes).toHaveCSS("opacity", "1");
  await expectHighlightsFit(notes, page);
  await saveScreenshot(page, "whats-new-1920x1080-dark.png");
  await page.keyboard.press("Escape");
  await expect(notes).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.emulateMedia({ colorScheme: "light" });
  await expect(notes).toHaveCSS("opacity", "1");
  await saveScreenshot(page, "whats-new-1920x1080-light.png");
  await notes.getByRole("button", { name: "Close release highlights", exact: true }).click();
  await expect(notes).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("a version change shows one popup, fits the minimum window, and does not repeat after reload", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.addInitScript(() => {
    if (localStorage.getItem("sharppy-workspace-ui") === null) localStorage.setItem("sharppy-workspace-ui", JSON.stringify({ state: { lastVersion: "3.0.0" }, version: 0 }));
  });
  await page.goto("/");
  const notes = page.getByRole("dialog", { name: `What’s new in ${version}` });
  await expect(notes).toBeVisible();
  await expectHighlightsFit(notes, page);
  await notes.getByRole("button", { name: "Got it" }).click();
  await page.reload();
  await expect(notes).toHaveCount(0);
  await expect(page.getByTestId("workspace-panel")).toHaveAttribute("aria-label", "Overview");
});

test("a saved workspace without version metadata opens the upgrade highlights", async ({ page }) => {
  await page.addInitScript(() => {
    if (localStorage.getItem("sharppy.prefs.v1") === null) localStorage.setItem("sharppy.prefs.v1", JSON.stringify({ version: 1 }));
  });
  await page.goto("/");
  const notes = page.getByRole("dialog", { name: `What’s new in ${version}` });
  await expect(notes).toBeVisible();
});

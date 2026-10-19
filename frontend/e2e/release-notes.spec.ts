import appManifest from "../package.json" with { type: "json" };
import releaseNotes from "../release-notes.json" with { type: "json" };
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { boxOf, isInside, viewportBox } from "./support/ui";

const { version } = appManifest;

test("a fresh install stays on Overview and release highlights can be reopened from Updates", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/");
  const notes = page.getByRole("dialog", { name: `What’s new in ${version}` });
  await expect(notes).toHaveCount(0);
  await expect(page.getByTestId("workspace-panel")).toHaveAttribute("aria-label", "Overview");
  await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("button", { name: "Updates", exact: true }).click();
  await expect(page.getByRole("heading", { name: `What’s new in ${version}` })).toBeVisible();
  await expect(page.getByText(releaseNotes[version as keyof typeof releaseNotes].summary)).toBeVisible();
  await saveScreenshot(page, "updates-1920x1080-dark.png");
  const trigger = page.getByRole("button", { name: "View highlights", exact: true });
  await trigger.click();
  await expect(notes).toBeFocused();
  await expect(notes).toHaveCSS("opacity", "1");
  await expect(notes.getByRole("listitem")).toHaveCount(5);
  expect(isInside(await boxOf(notes), viewportBox(page))).toBe(true);
  await saveScreenshot(page, "whats-new-1920x1080-dark.png");
  await page.keyboard.press("Escape");
  await expect(notes).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.emulateMedia({ colorScheme: "light" });
  await expect(notes).toHaveCSS("opacity", "1");
  await saveScreenshot(page, "whats-new-1920x1080-light.png");
  await notes.getByRole("button", { name: "Got it" }).click();
  await expect(notes).toHaveCount(0);
});

test("a version change shows one popup, fits the minimum window, and does not repeat after reload", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.addInitScript(() => {
    if (localStorage.getItem("sharppy-workspace-ui") === null) localStorage.setItem("sharppy-workspace-ui", JSON.stringify({ state: { lastVersion: "3.0.0" }, version: 0 }));
  });
  await page.goto("/");
  const notes = page.getByRole("dialog", { name: `What’s new in ${version}` });
  await expect(notes).toBeVisible();
  expect(isInside(await boxOf(notes), viewportBox(page))).toBe(true);
  expect(isInside(await boxOf(notes.getByRole("button", { name: "Got it" })), await boxOf(notes))).toBe(true);
  await notes.getByRole("button", { name: "Got it" }).click();
  await page.reload();
  await expect(notes).toHaveCount(0);
  await expect(page.getByTestId("workspace-panel")).toHaveAttribute("aria-label", "Overview");
});

test("an existing workspace receives highlights even when its old release did not track versions", async ({ page }) => {
  await page.addInitScript(() => {
    if (localStorage.getItem("sharppy.prefs.v1") === null) localStorage.setItem("sharppy.prefs.v1", JSON.stringify({ version: 1 }));
  });
  await page.goto("/");
  const notes = page.getByRole("dialog", { name: `What’s new in ${version}` });
  await expect(notes).toBeVisible();
  await notes.getByRole("button", { name: "Got it" }).click();
  await page.reload();
  await expect(notes).toHaveCount(0);
});

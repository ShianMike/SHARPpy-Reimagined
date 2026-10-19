import { waitForMapDrawn } from "./support/app";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { boxOf, isInside, viewportBox } from "./support/ui";

for (const { width, height, theme } of [
  { width: 1280, height: 800, theme: "dark" },
  { width: 1920, height: 1080, theme: "dark" },
  { width: 1920, height: 1080, theme: "light" },
] as const) {
  test(`Overview fits ${String(width)} × ${String(height)} in ${theme} theme`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    await page.goto("/");
    const panel = page.getByTestId("workspace-panel");
    await expect(panel.getByRole("heading", { name: "HRRR F000" })).toBeVisible();
    const cards = await Promise.all(["Model Viewer", "Live Tracking", "Soundings"].map((name) => boxOf(panel.getByRole("region", { name, exact: true }))));
    const panelBox = await boxOf(panel);
    expect(isInside(panelBox, viewportBox(page))).toBe(true);
    for (const [index, card] of cards.entries()) {
      expect(isInside(card, panelBox)).toBe(true);
      expect(card.y).toBeCloseTo(cards[0]?.y ?? 0, 1);
      expect(card.height).toBeCloseTo(cards[0]?.height ?? 0, 1);
      if (index > 0) expect(card.x).toBeGreaterThan((cards[index - 1]?.x ?? 0) + (cards[index - 1]?.width ?? 0));
    }
    const buttons = panel.getByRole("button");
    for (const button of await buttons.all()) expect(isInside(await boxOf(button), panelBox)).toBe(true);
    expect(await panel.locator("[class*='_page_']").evaluate((element) => element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight)).toBe(true);
    await waitForMapDrawn(page);
    await saveScreenshot(page, `overview-${String(width)}x${String(height)}-${theme}.png`);
  });
}

test("Overview disclosures and workspace actions support keyboard navigation", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  const panel = page.getByTestId("workspace-panel");
  const availability = panel.locator("summary").filter({ hasText: "Model availability" });
  await availability.focus();
  await page.keyboard.press("Enter");
  await expect(panel.getByRole("heading", { name: "Model backends" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Refresh statuses" })).toBeVisible();
  await availability.focus();
  await page.keyboard.press("Enter");
  await expect(panel.getByRole("heading", { name: "Model backends" })).toHaveCount(0);
  const activity = panel.locator("summary").filter({ hasText: "Recent activity" });
  await activity.focus();
  await page.keyboard.press("Space");
  await panel.getByRole("button", { name: "View logs" }).click();
  await expect(panel).toHaveAttribute("aria-label", "Logs");
  const rail = page.getByRole("navigation", { name: "Workspaces" });
  for (const [action, destination] of [["Open Model Viewer", "Model Viewer"], ["Open Live Tracking", "Live Tracking"], ["Open Settings", "Settings"]] as const) {
    await rail.getByRole("button", { name: "Overview", exact: true }).click();
    await panel.getByRole("button", { name: action, exact: true }).click();
    await expect(rail.getByRole("button", { name: destination, exact: true })).toHaveAttribute("aria-current", "page");
  }
});

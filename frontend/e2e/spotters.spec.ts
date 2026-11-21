import { deckLayers, waitForMapDrawn } from "./support/app";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { hoverAt, layerSettings, turnOn } from "./support/ui";

test("Spotter Network is a current-time layer in Live Tracking", async ({ page }) => {
  await page.goto("/");
  const workspaces = page.getByRole("navigation", { name: "Workspaces" });
  const layers = page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true });
  await workspaces.getByRole("button", { name: "Model Viewer", exact: true }).click();
  await waitForMapDrawn(page);
  await layers.click();
  await expect(page.getByTestId("layer-row-spotter-network")).toHaveCount(0);
  await layers.click();

  await workspaces.getByRole("button", { name: "Live Tracking", exact: true }).click();
  await turnOn(page, "spotter-network");
  const editor = await layerSettings(page, "spotter-network");
  await expect(editor.getByTestId("spotter-network-shown")).toContainText(/Active spotters\s*1/u);
  await expect(editor.getByTestId("spotter-network-shown")).toContainText(/Chase-cam spotters\s*1/u);
  await expect.poll(() => deckLayers(page)).toContain("spotter-network");
  await hoverAt(page, -97.3, 35.3);
  await expect(page.getByRole("tooltip").getByText("Morgan Reed", { exact: true })).toBeVisible();
  await expect(page.getByRole("tooltip").getByText("Stationary", { exact: true })).toBeVisible();
  await hoverAt(page, -98, 35.5);
  await expect(page.getByRole("tooltip").getByText("Taylor Lake", { exact: true })).toBeVisible();
  await page.mouse.down();
  await page.mouse.up();
  const card = page.getByRole("dialog", { name: "Spotter details for Taylor Lake" });
  await expect(card.getByText("NW 320°", { exact: true })).toBeVisible();
  await card.getByText("Last position", { exact: true }).hover();
  await expect(card).toBeVisible();
  await saveScreenshot(page, "browser-spotter-details.png");
  await page.keyboard.press("Escape");
  await expect(card).toHaveCount(0);

  await page.getByRole("slider", { name: "Live history" }).press("ArrowLeft");
  await expect.poll(() => deckLayers(page)).not.toContain("spotter-network");
  await layerSettings(page, "spotter-network");
  await page.getByRole("button", { name: "Return to Live", exact: true }).click();
  await expect.poll(() => deckLayers(page)).toContain("spotter-network");
  await saveScreenshot(page, "browser-spotter-network.png");
});

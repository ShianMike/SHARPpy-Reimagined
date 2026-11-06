import { waitForMapDrawn } from "./support/app";
import { saveScreenshot } from "./support/screenshots";
import { layerRow, turnOn } from "./support/ui";
import { expect, test } from "./support/test";

test.use({ viewport: { width: 1920, height: 1080 } });

for (const theme of ["dark", "light"] as const) {
  test(`isobars have independent controls and a pressure legend in ${theme} maps`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.goto("/");
    const workspaces = page.getByRole("navigation", { name: "Workspaces" });
    await workspaces.getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMapDrawn(page);
    const row = await layerRow(page, "isobars");
    await expect(row.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    await turnOn(page, "isobars");
    await expect(row.getByText(/Mean sea-level pressure · 4 hPa intervals/u)).toBeVisible();
    await row.getByRole("slider", { name: "Isobars opacity", exact: true }).fill("0.55");
    await expect(row.getByRole("slider", { name: "Isobars opacity", exact: true })).toHaveValue("0.55");
    await saveScreenshot(page, `isobars-controls-${theme}.png`);
    await page.getByRole("group", { name: "Layer categories" }).getByRole("button", { name: "Legend", exact: true }).click();
    const key = page.getByTestId("legend-isobars");
    await expect(key.getByText("Mean sea-level pressure · lines every 4 hPa. Pressure labels are in hPa when available.", { exact: true })).toBeVisible();
    await expect(key.getByText("55% opacity", { exact: true })).toBeVisible();
    await saveScreenshot(page, `isobars-legend-${theme}.png`);
    await workspaces.getByRole("button", { name: "Live Tracking", exact: true }).click();
    const layers = page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true });
    if (await layers.getAttribute("aria-expanded") !== "true") await layers.click();
    await page.getByRole("group", { name: "Layer categories" }).getByRole("button", { name: "Weather", exact: true }).click();
    await expect(page.getByRole("switch", { name: "Isobars", exact: true })).toHaveCount(0);
  });
}

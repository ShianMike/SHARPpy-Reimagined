// UI helpers shared by the specs: layer rows on the forecast map, choosing a
// station through the search, and box geometry for the layout checks.
import type { Locator, Page } from "@playwright/test";
import { projectPoint } from "./app";
import { expect } from "./test";

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A token resolved through a real property, so the minifier's spelling does not matter. */
export async function resolved(page: Page, token: string, property: "color" | "width" | "z-index"): Promise<string> {
  return page.evaluate(
    ({ name, prop }) => {
      const probe = document.createElement("div");
      probe.style.setProperty("position", "absolute");
      probe.style.setProperty(prop, `var(${name})`);
      document.body.append(probe);
      const value = getComputedStyle(probe).getPropertyValue(prop);
      probe.remove();
      return value;
    },
    { name: token, prop: property },
  );
}

/** The layer panel's row for one layer id, with its settings exposed. */
export async function layerRow(page: Page, id: string) {
  if (["radar", "satellite", "surface-plots", "warnings", "watches"].includes(id)) {
    await page.clock.setFixedTime(new Date("2026-10-04T21:00:00Z"));
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Live Tracking", exact: true }).click();
  }
  const layers = page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true });
  if (await layers.getAttribute("aria-expanded") !== "true") await layers.click();
  const mapDetail = ["stations", "station-labels", "model-domain", "place-labels", "boundaries"].includes(id);
  await page.getByRole("group", { name: "Layer categories" }).getByRole("button", { name: mapDetail ? "Map details" : "Weather", exact: true }).click();
  const row = page.getByTestId(`layer-row-${id}`);
  await expect(row).toBeVisible();
  const settings = row.getByRole("button", { name: /settings$/u });
  if (await settings.count() > 0 && await settings.getAttribute("aria-expanded") === "false") {
    await settings.click();
  }
  return row;
}

/** Turns a layer on through its switch. */
export async function turnOn(page: Page, id: string): Promise<void> {
  const row = await layerRow(page, id);
  const toggle = row.getByRole("switch").first();
  if (await toggle.getAttribute("aria-checked") !== "true") await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  const settings = row.getByRole("button", { name: /settings$/u });
  if (await settings.count() > 0 && await settings.getAttribute("aria-expanded") === "false") await settings.click();
}

/** Moves the pointer to a map position; no-op when the point is off the map. */
export async function hoverAt(page: Page, longitude: number, latitude: number): Promise<void> {
  const layers = page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true });
  if (await layers.getAttribute("aria-expanded") === "true") await layers.click();
  const point = await projectPoint(page, longitude, latitude);
  expect(point, `(${String(longitude)}, ${String(latitude)}) projects onto the map`).not.toBeNull();
  if (point !== null) {
    await page.mouse.move(point.x, point.y);
  }
}

/** Ctrl+K, type the query, Enter: the first (or only) result is chosen. */
export async function chooseThroughSearch(page: Page, query: string): Promise<void> {
  const input = page.getByRole("combobox", { name: "Search stations" });
  await page.keyboard.press("Control+k");
  await expect(input).toBeEnabled();
  await expect(input).toBeFocused();
  await input.fill(query);
  await expect(page.getByRole("listbox", { name: "Station results" })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("listbox", { name: "Station results" })).toHaveCount(0);
}

export async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (box === null) {
    throw new Error(`no bounding box for ${locator.toString()}`);
  }
  return box;
}

/** Sub-pixel tolerance for layout rounding. */
const EPSILON = 0.5;

export function isInside(inner: Box, outer: Box): boolean {
  return (
    inner.x >= outer.x - EPSILON &&
    inner.y >= outer.y - EPSILON &&
    inner.x + inner.width <= outer.x + outer.width + EPSILON &&
    inner.y + inner.height <= outer.y + outer.height + EPSILON
  );
}

/** The area two boxes share; boxes that only touch share none. */
export function overlapArea(a: Box, b: Box): number {
  const width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return width > EPSILON && height > EPSILON ? width * height : 0;
}

export function viewportBox(page: Page): Box {
  const size = page.viewportSize();
  if (size === null) {
    throw new Error("the page has no viewport");
  }
  return { x: 0, y: 0, width: size.width, height: size.height };
}

// The toolbar's computed glass: its backdrop filter and its fill.
import type { Page } from "@playwright/test";

export interface Glass {
  filter: string;
  red: number;
  green: number;
  blue: number;
  alpha: number;
}

export async function toolbarGlass(page: Page): Promise<Glass> {
  return page.getByRole("banner", { name: "SHARPpy Reimagined" }).locator('[data-material="glass"]').first().evaluate((element) => {
    const style = getComputedStyle(element);
    const [red = 0, green = 0, blue = 0, alpha = 1] = (style.backgroundColor.match(/[\d.]+/gu) ?? []).map(Number);
    return { filter: style.backdropFilter, red, green, blue, alpha };
  });
}

/** Elements whose computed backdrop filter is anything but none. */
export async function elementsWithBackdropFilter(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll("*"))
      .filter((element) => {
        const style = getComputedStyle(element);
        // -webkit-backdrop-filter is an alias of the same property in Chromium.
        return style.backdropFilter !== "none";
      })
      .map((element) => `${element.tagName.toLowerCase()}#${element.id}`),
  );
}

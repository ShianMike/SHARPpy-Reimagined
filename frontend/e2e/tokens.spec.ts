// Design tokens as shipped (DESIGN-tooling T8): computed :root tokens, the
// glass fill and filter per forced profile, and tabular numerals on ids.
import type { Page } from "@playwright/test";
import { effectiveProfile, MARKS, presetPreferences, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { toolbarGlass } from "./support/glass";
import { expect, test } from "./support/test";
import { chooseThroughSearch } from "./support/ui";

/** A token resolved through a real property, so the minifier's spelling does not matter. */
async function resolved(page: Page, token: string, property: "color" | "width" | "z-index"): Promise<string> {
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

test("the :root tokens resolve to the design values", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMark(page, MARKS.shellPainted);
  expect(await resolved(page, "--surface-0", "color")).toBe("rgb(20, 21, 22)");
  expect(await resolved(page, "--text-primary", "color")).toBe("rgb(242, 242, 242)");
  expect(await resolved(page, "--accent", "color")).toBe("rgb(184, 84, 11)");
  expect(await resolved(page, "--warning", "color")).toBe("rgb(210, 153, 34)");
  expect(await resolved(page, "--space-2", "width")).toBe("8px");
  expect(await resolved(page, "--toolbar-h", "width")).toBe("56px");
  expect(await resolved(page, "--dock-w", "width")).toBe("56px");
  expect(await resolved(page, "--z-notice", "z-index")).toBe("15");
  expect(await resolved(page, "--z-popover", "z-index")).toBe("30");
  const toolbarHeight = await page.getByRole("banner", { name: "SHARPpy Reimagined" }).evaluate((element) => element.getBoundingClientRect().height);
  expect(toolbarHeight).toBe(56);
  expectWarnings(consoleMessages, []);
});

for (const override of ["high", "balanced", "low"] as const) {
  test(`the floating controls use the ${override} glass profile`, async ({ page, consoleMessages }) => {
    await presetPreferences(page, { graphicsOverride: override });
    await page.goto("/");
    await waitForMark(page, MARKS.stationsRendered);
    expect(await effectiveProfile(page)).toEqual({ mode: "full", profile: override, reasons: ["Chosen in Settings"] });
    const glass = await toolbarGlass(page);
    expect(glass).toMatchObject({ red: 25, green: 31, blue: 40, alpha: { high: 0.62, balanced: 0.68, low: 0.97 }[override] });
    expect(glass.filter).toBe({ high: "blur(6px) saturate(1.12)", balanced: "blur(4px) saturate(1.08)", low: "none" }[override]);
    expectWarnings(consoleMessages, []);
  });
}

test("ids and counts use tabular numerals", async ({ page, consoleMessages }) => {
  await page.goto("/");
  await waitForMark(page, MARKS.stationsRendered);
  await chooseThroughSearch(page, "72357");
  const numeric = async (locator: ReturnType<Page["getByTestId"]>): Promise<string> =>
    locator.evaluate((element) => getComputedStyle(element).fontVariantNumeric);
  expect(await numeric(page.getByRole("complementary", { name: "Inspector" }).getByText("72357", { exact: true }))).toBe(
    "tabular-nums",
  );
  expectWarnings(consoleMessages, []);
});

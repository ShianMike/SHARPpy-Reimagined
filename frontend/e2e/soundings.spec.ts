// Sounding forms, provider errors, file opening, and the backend-rendered image.
import { join } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { MARKS, nextFrames, waitForMapDrawn, waitForMark } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { expect, test } from "./support/test";
import { boxOf, chooseThroughSearch, isInside, overlapArea, viewportBox } from "./support/ui";

/** The 49-time HRRR BUFKIT file in the repository's examples. */
const BUFKIT = join("..", "examples", "soundings", "hrrr_kbvo_20260625_06z.buf");
/** Valid times in the multi-time fixture summary, the file above as sharpmod read it. */
const BUFKIT_TIMES = 49;

function railItem(page: Page, name: string): Locator {
  return page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name });
}

async function showWorkspace(page: Page, name: string): Promise<void> {
  await railItem(page, name).click();
  await expect(railItem(page, name)).toHaveAttribute("aria-current", "page");
}

/** The image is decoded and drawn at its own aspect ratio, inside its stage. */
async function expectImageDrawn(stage: Locator): Promise<void> {
  const image = stage.getByTestId("sounding-image");
  await expect(image).toBeVisible();
  await expect
    .poll(() => image.evaluate((element) => ((element as HTMLImageElement).complete ? (element as HTMLImageElement).naturalWidth : 0)))
    .toBeGreaterThan(0);
  const natural = await image.evaluate((element) => {
    const img = element as HTMLImageElement;
    return img.naturalWidth / img.naturalHeight;
  });
  const box = await boxOf(image);
  expect(Math.abs(box.width / box.height - natural), "drawn at the image's aspect ratio").toBeLessThan(0.02);
  expect(isInside(box, await boxOf(stage)), "the image stays inside its stage").toBe(true);
}

/** The Observed form's Open sounding, then the viewer's image for the new sounding. */
async function openObserved(page: Page): Promise<void> {
  const button = page.getByTestId("open-observed");
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.getByText(/^Fetching 72357 \d{4}-\d\d-\d\d (00|12)Z$/u)).toBeVisible();
  await expectImageDrawn(page.getByTestId("sounding-viewer").getByTestId("sounding-stage"));
}

test.describe("at 1440 by 900", () => {
  test("open soundings scroll independently and keep source controls and the list heading fixed", async ({ page }) => {
    await page.goto("/");
    await waitForMark(page, MARKS.shellPainted);
    await showWorkspace(page, "Soundings");
    for (let index = 0; index < 12; index++) {
      await page.getByRole("textbox", { name: "Station" }).fill(String(72357 + index));
      await page.getByTestId("open-observed").click();
      await expect(page.getByTestId("open-soundings").getByRole("listitem")).toHaveCount(index + 1);
    }
    for (const height of [900, 600]) {
      await page.setViewportSize({ width: 1440, height });
      const side = page.locator("#sounding-sources");
      const list = page.getByTestId("open-soundings");
      const heading = page.getByRole("heading", { name: "Open soundings 12" });
      const before = await boxOf(page.getByRole("tablist", { name: "Source" }));
      const headingBefore = await boxOf(heading);
      expect(await list.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
      await list.getByRole("button", { name: /^72357 /u }).focus();
      await nextFrames(page);
      expect(await list.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
      expect(await side.evaluate((element) => element.scrollTop === 0 && element.scrollHeight <= element.clientHeight)).toBe(true);
      expect(await boxOf(page.getByRole("tablist", { name: "Source" }))).toEqual(before);
      expect(await boxOf(heading)).toEqual(headingBefore);
      await expect(page.getByRole("button", { name: "Hide sources" })).toBeVisible();
      await page.getByRole("tab", { name: "Model", exact: true }).click();
      await page.getByTestId("open-model").scrollIntoViewIfNeeded();
      expect(await side.evaluate((element) => element.scrollTop === 0 && element.scrollHeight <= element.clientHeight)).toBe(true);
      await expect(heading).toBeVisible();
      await expect(page.getByRole("tablist", { name: "Source" })).toBeVisible();
      await page.getByRole("tab", { name: "Obs.", exact: true }).click();
    }
    await saveScreenshot(page, "sounding-list-independent-scroll.png");
  });

  test("fit sounding settles at fractional viewport sizes without a scrollbar resize loop", async ({ page }) => {
    await page.goto("/");
    await waitForMark(page, MARKS.shellPainted);
    await showWorkspace(page, "Soundings");
    await page.getByRole("textbox", { name: "Station" }).fill("72357");
    await openObserved(page);
    const stage = page.getByTestId("sounding-stage");
    await stage.evaluate((element) => {
      const image = element.querySelector("img");
      if (image === null) throw new Error("Sounding image missing");
      const height = 500.5;
      element.style.flex = "none";
      element.style.height = `${String(height)}px`;
      element.style.width = `${String((height - 24) * image.naturalWidth / image.naturalHeight + 24 + 0.25)}px`;
    });
    for (let frame = 0; frame < 10; frame++) await nextFrames(page);
    const fit = await stage.evaluate((element) => {
      const viewport = element.firstElementChild;
      const image = element.querySelector("img");
      if (viewport === null || image === null) throw new Error("Sounding viewport missing");
      const box = viewport.getBoundingClientRect();
      const drawn = image.getBoundingClientRect();
      return { width: box.width, height: box.height, imageWidth: drawn.width, imageHeight: drawn.height };
    });
    expect(fit.imageWidth + 24).toBeLessThanOrEqual(fit.width);
    expect(fit.imageHeight + 24).toBeLessThanOrEqual(fit.height);
    await page.getByRole("button", { name: "Actual pixels (100%)", exact: true }).click();
    await page.getByRole("button", { name: "Fit sounding to view", exact: true }).click();
    await nextFrames(page);
    await expectImageDrawn(stage);
    await showWorkspace(page, "Logs");
    await page.getByRole("button", { name: "Errors", exact: true }).click();
    await expect(page.getByRole("heading", { name: "No matching events", exact: true })).toBeVisible();
  });

  test("observed: from a map station to sharpmod's image and parcel selection", async ({
    page,
    consoleMessages,
  }) => {
    await page.goto("/");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMapDrawn(page);
    await chooseThroughSearch(page, "72357");
    const inspector = page.getByRole("complementary", { name: "Inspector" });
    await inspector.getByRole("button", { name: "Open sounding" }).click();
    await expect(railItem(page, "Soundings")).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("tab", { name: "Obs." })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("textbox", { name: "Station" })).toHaveValue("72357");
    await expect(page.getByTestId("observed-station-name")).toHaveText("Norman/Max Westheimer A, Ok., Usa");
    // Choosing Open sounding in the inspector fills the form; it does not fetch.
    await expect(page.getByText("No sounding open")).toBeVisible();

    await openObserved(page);
    await expect(page.getByTestId("sounding-title")).toHaveText("72357 (example data)");
    await expect(page.getByTestId("open-soundings").getByRole("listitem")).toHaveCount(1);
    await page.getByText("Chart panels", { exact: true }).filter({ visible: true }).click();
    const parcel = page.getByRole("group", { name: "Parcel" });
    await expect(parcel.getByRole("button", { name: "MU" })).toHaveAttribute("aria-pressed", "true");
    await nextFrames(page);
    await saveScreenshot(page, "browser-sounding.png");

    await parcel.getByRole("button", { name: "ML" }).click();
    await expect(parcel.getByRole("button", { name: "ML" })).toHaveAttribute("aria-pressed", "true");
    await expect(parcel.getByRole("button", { name: "MU" })).toHaveAttribute("aria-pressed", "false");
    await expectImageDrawn(page.getByTestId("sounding-viewer").getByTestId("sounding-stage"));

    expectWarnings(consoleMessages, []);
  });

  test("a BUFKIT file: the first time rendered with its time count", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMark(page, MARKS.shellPainted);
    await showWorkspace(page, "Soundings");
    await page.getByRole("tab", { name: "File" }).click();
    await page.locator('input[type="file"]').setInputFiles(BUFKIT);
    await page.getByTestId("open-file").click();
    await expectImageDrawn(page.getByTestId("sounding-viewer").getByTestId("sounding-stage"));
    await expect(page.getByTestId("sounding-title")).toHaveText("hrrr_kbvo_20260625_06z.buf");
    await expect(page.getByTestId("sounding-viewer")).toContainText(`first of ${String(BUFKIT_TIMES)} times`);

    expectWarnings(consoleMessages, []);
  });

  test("a model sounding at the selected station", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await waitForMapDrawn(page);
    await chooseThroughSearch(page, "72357");
    await showWorkspace(page, "Soundings");
    await page.getByRole("tab", { name: "Model" }).click();
    const form = page.getByRole("form", { name: "Model sounding" });
    await expect(form.getByRole("combobox", { name: "Model" })).toBeVisible();
    const atStation = form.getByRole("switch", { name: "At station 72357" });
    await expect(atStation).toBeChecked();
    await expect(form.getByRole("textbox", { name: "Latitude, degrees north" })).toHaveCount(0);
    await page.getByTestId("open-model").click();
    await expectImageDrawn(page.getByTestId("sounding-viewer").getByTestId("sounding-stage"));
    await expect(page.getByTestId("sounding-title")).toHaveText(/\(example data\)$/u);
    await nextFrames(page);
    await saveScreenshot(page, "browser-sounding-model.png");
    expectWarnings(consoleMessages, []);
  });

  test("sounding-error: the backend's reason, and no Retry", async ({ page, consoleMessages }) => {
    await page.goto("/?scenario=sounding-error");
    await waitForMark(page, MARKS.shellPainted);
    await showWorkspace(page, "Soundings");
    await page.getByRole("textbox", { name: "Station" }).fill("72357");
    await page.getByTestId("open-observed").click();
    const notice = page.getByTestId("observed-error");
    await expect(notice).toContainText("The sounding could not be opened");
    await expect(notice).toContainText("Retrying will not help.");
    await expect(notice.getByRole("button", { name: "Retry" })).toHaveCount(0);
    await expect(page.getByText("No sounding open")).toBeVisible();
    await nextFrames(page);
    await saveScreenshot(page, "browser-sounding-error.png");
    expectWarnings(consoleMessages, [{ pattern: /^\[backend\] SOUNDING_UNAVAILABLE in open_sounding/u, count: 1 }]);
  });
});

test.describe("at the 1280 by 800 minimum window", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("the Soundings workspace fits the minimum window", async ({
    page,
    consoleMessages,
  }) => {
    await page.goto("/");
    await waitForMark(page, MARKS.shellPainted);
    await showWorkspace(page, "Soundings");
    await page.getByRole("textbox", { name: "Station" }).fill("72357");
    await openObserved(page);
    const viewport = viewportBox(page);
    await expect(page.getByRole("region", { name: "Forecast timeline" })).toBeHidden();
    const surfaces = {
      source: page.getByRole("region", { name: "Open a sounding" }),
      viewer: page.getByTestId("sounding-viewer"),
    };
    for (const [name, locator] of Object.entries(surfaces)) {
      const box = await boxOf(locator);
      expect(isInside(box, viewport), `${name} inside the window`).toBe(true);
    }
    expect(overlapArea(await boxOf(surfaces.source), await boxOf(surfaces.viewer))).toBe(0);
    await nextFrames(page);
    await saveScreenshot(page, "browser-sounding-min.png");

    expectWarnings(consoleMessages, []);
  });
});

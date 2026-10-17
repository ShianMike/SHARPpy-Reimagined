// The forecast map against the fixture backend (MIGRATION phases 2 to 8): the
// central forecast time in the toolbar and the timeline, the model field with
// its legend, state, and pointer value, SPC outlooks, the radar mosaic, the
// stacking order, a forecast point that opens a model sounding, playback,
// two and four synchronized panels, and station availability.
import { deckLayers, labels, nextFrames, presetPreferences, projectPoint, waitForMapDrawn } from "./support/app";
import { expectWarnings } from "./support/console";
import { saveScreenshot } from "./support/screenshots";
import { hoverAt, layerRow, turnOn } from "./support/ui";
import { expect, test } from "./support/test";


test.describe("the forecast map", () => {
  test("the forecast setup and timeline share run limits and time", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    const hours = page.getByRole("slider", { name: "Forecast hour", exact: true });
    await expect(hours).toHaveAttribute("max", "18");
    await page.getByRole("button", { name: "Next frame", exact: true }).click();
    await expect(page.getByRole("button", { name: "Choose forecast hour" })).toContainText("F001");
    await expect(page.getByLabel("Valid time")).toContainText("10-04 17Z");
    await page.getByRole("button", { name: "Choose run" }).click();
    await page.getByRole("button", { name: "Run 10-04 12Z", exact: true }).click();
    await page.getByRole("button", { name: "Choose forecast hour" }).click();
    await page.getByRole("button", { name: "Select forecast hour 30", exact: true }).click();
    await expect(hours).toHaveAttribute("max", "48");
    await expect(page.getByLabel("Valid time")).toContainText("10-05 18Z");
    await page.getByRole("button", { name: "Choose run" }).click();
    await page.getByRole("button", { name: "Run 10-04 16Z", exact: true }).click();
    await expect(hours).toHaveAttribute("max", "18");
    await expect(hours).toHaveValue("18");
    await page.getByRole("button", { name: "Choose model" }).click();
    await page.getByRole("button", { name: "Choose GFS", exact: true }).click();
    await expect(page.getByRole("button", { name: "Choose model" })).toContainText("GFS");
    await expect(page.getByRole("button", { name: "Choose parameter" })).toBeDisabled();
    expectWarnings(consoleMessages, []);
  });

  test("the model field: product, colour scale, frame state, pointer value", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    await turnOn(page, "model-field");
    const row = await layerRow(page, "model-field");
    await expect(row.getByTestId("field-shown")).toContainText("F000 of the 10-04 16Z run, valid 10-04 16Z");
    await expect(row.getByTestId("field-legend")).toBeVisible();
    await expect.poll(() => deckLayers(page)).toContain("model-field-smooth");
    // The boundaries go over a drawn field.
    expect(await deckLayers(page)).toContain("boundaries-states");
    await expect(page.getByTestId("panel-chip-1")).toHaveCount(0);
    const scale = page.getByRole("complementary", { name: "Parameter scale" });
    await expect(scale).toContainText("dBZ");

    await row.getByRole("combobox", { name: "Product" }).click();
    await page.getByRole("listbox", { name: "Product" }).locator('[value="tmp-2m"]').click();
    await expect(row.getByTestId("field-shown")).toBeVisible();
    await expect(row).toContainText("°F");
    await expect(scale).toContainText("°F");
    await nextFrames(page);
    await saveScreenshot(page, "browser-forecast-field.png");
    // A contoured product: the lines draw with the colour, and the row says so.
    await row.getByRole("combobox", { name: "Product" }).click();
    await page.getByRole("listbox", { name: "Product" }).locator('[value="hgt-wind-500"]').click();
    await expect(row).toContainText("Contours every 6 dam.");
    await expect.poll(() => deckLayers(page)).toContain("model-field-contours");
    expectWarnings(consoleMessages, []);
  });

  test("an unpublished field frame says so, with no retry", async ({ page, consoleMessages }) => {
    await page.goto("/?scenario=field-error");
    await waitForMapDrawn(page);
    await turnOn(page, "model-field");
    const notice = page.getByTestId("field-error");
    await expect(notice).toContainText("The model field could not be loaded");
    await expect(notice.getByRole("button", { name: "Retry" })).toHaveCount(0);
    await expect(notice.getByTestId("error-no-retry")).toHaveText("Retrying will not help.");
    await expect(page.getByRole("complementary", { name: "Parameter scale" })).toContainText("Unavailable");
    expect(await deckLayers(page)).not.toContain("model-field-smooth");
    expectWarnings(consoleMessages, [{ pattern: /^\[backend\] FIELD_UNAVAILABLE in get_weather_field/u }]);
  });

  test("SPC outlooks, the radar mosaic, and the stacking order", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    await turnOn(page, "spc-cat");
    await expect(page.getByTestId("outlook-shown-cat")).toContainText("Day 1");
    await expect.poll(() => deckLayers(page)).toContain("spc-cat-fill");
    await hoverAt(page, -96.5, 34.5);
    await expect(page.getByTestId("outlook-tooltip")).toContainText("SLGT");
    await expect(page.getByTestId("outlook-tooltip")).toContainText("Slight Risk");
    await turnOn(page, "spc-hail");
    await expect(page.getByTestId("outlook-none-hail")).toContainText("SPC published no outlook");
    await turnOn(page, "spc-torn");
    await expect.poll(() => deckLayers(page)).toEqual(expect.arrayContaining([
      "spc-torn-hatch-1", "spc-torn-hatch-2", "spc-torn-hatch-3",
    ]));

    await turnOn(page, "radar");
    await expect(page.getByTestId("radar-shown")).toContainText("Composite reflectivity");
    await expect.poll(() => deckLayers(page)).toContain("radar");
    // Radar starts above the field and under the outlooks; Move up twice puts it over the categorical one.
    const below = async (a: string, b: string): Promise<boolean> => {
      const ids = await deckLayers(page);
      return ids.indexOf(a) < ids.indexOf(b);
    };
    expect(await below("radar", "spc-cat-fill")).toBe(true);
    const radarRow = await layerRow(page, "radar");
    await radarRow.getByRole("button", { name: "Move Radar mosaic up" }).click();
    await radarRow.getByRole("button", { name: "Move Radar mosaic up" }).click();
    await expect.poll(() => below("spc-cat-fill", "radar")).toBe(true);
    await nextFrames(page);
    await saveScreenshot(page, "browser-forecast-overlays.png");
    expectWarnings(consoleMessages, []);
  });

  test("single-site radar: the scope switch, the site picker, and its own frame", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    await turnOn(page, "radar");
    const row = await layerRow(page, "radar");
    // Switching scope clears the mosaic product; the site scope takes its own default.
    await row.getByRole("combobox", { name: "Radar" }).click();
    await page.getByRole("option", { name: "Single site", exact: true }).click();
    await expect(row.getByRole("combobox", { name: "Site" })).toBeVisible();
    await expect(row.getByTestId("radar-shown")).toContainText("Base reflectivity");
    await expect(row.getByTestId("radar-shown")).toContainText("One antenna's own imagery");
    await expect.poll(() => deckLayers(page)).toContain("radar");
    // Pin an antenna by identifier: the status names the site it asked for.
    await row.getByRole("combobox", { name: "Site" }).click();
    await page.getByRole("option", { name: "KTLX", exact: true }).click();
    await expect(row.getByTestId("radar-shown")).toContainText("Base reflectivity");
    await expect.poll(() => deckLayers(page)).toContain("radar");
    await nextFrames(page);
    await saveScreenshot(page, "browser-forecast-radar-site.png");
    // Back to the mosaic: the site picker goes away and the mosaic returns.
    await row.getByRole("combobox", { name: "Radar" }).click();
    await page.getByRole("option", { name: "CONUS mosaic", exact: true }).click();
    await expect(row.getByRole("combobox", { name: "Site" })).toHaveCount(0);
    await expect(row.getByTestId("radar-shown")).toContainText("Composite reflectivity");
    expectWarnings(consoleMessages, []);
  });

  test("the GOES satellite layer: channel, scan provenance, and stacking", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    await turnOn(page, "satellite");
    const row = await layerRow(page, "satellite");
    await expect(row.getByTestId("satellite-shown")).toContainText("G19");
    await expect(row.getByTestId("satellite-shown")).toContainText("87% view coverage");
    await expect.poll(() => deckLayers(page)).toContain("satellite");
    // The boundaries redraw over the drawn satellite frame.
    expect(await deckLayers(page)).toContain("boundaries-states");
    await row.getByRole("combobox", { name: "Channel" }).click();
    await page.getByRole("option", { name: "Visible (0.64 µm)", exact: true }).click();
    await expect(row.getByTestId("satellite-shown")).toContainText("Visible (0.64 µm)");
    await nextFrames(page);
    await saveScreenshot(page, "browser-forecast-satellite.png");
    expectWarnings(consoleMessages, []);
  });

  test("storm reports: window and hazard filter, with the marker readout", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    await turnOn(page, "storm-reports");
    const row = await layerRow(page, "storm-reports");
    await expect(row.getByTestId("reports-shown")).toContainText("4 of 4 reports");
    await expect.poll(() => deckLayers(page)).toContain("storm-reports");
    // The provider's own filter narrows the marks; the counts say so.
    await row.getByRole("combobox", { name: "Hazard" }).click();
    await page.getByRole("option", { name: "Tornado", exact: true }).click();
    await expect(row.getByTestId("reports-shown")).toContainText("1 of 4 reports");
    await row.getByRole("combobox", { name: "Event window" }).click();
    await page.getByRole("option", { name: "±1 hour", exact: true }).click();
    await expect(row.getByTestId("reports-shown")).toContainText("±1 hour");
    await row.getByRole("combobox", { name: "Hazard" }).click();
    await page.getByRole("option", { name: "All convective hazards", exact: true }).click();
    await nextFrames(page);
    await saveScreenshot(page, "browser-forecast-reports.png");
    expectWarnings(consoleMessages, []);
  });

  test("a forecast point picked on the map opens a model sounding there", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    await turnOn(page, "model-field");
    await expect(page.getByTestId("field-shown")).toContainText("F000");
    const point = await projectPoint(page, -108, 36);
    expect(point).not.toBeNull();
    if (point !== null) {
      await page.mouse.click(point.x, point.y);
    }
    const inspector = page.getByRole("complementary", { name: "Inspector" });
    // A click lands on a whole pixel, so the point is within a few hundredths of a degree.
    await expect(inspector).toContainText("Forecast point");
    await expect(inspector).toContainText(/3[56]\.\d\d° N/u);
    await expect(inspector.getByTestId("point-forecast")).toContainText("F000");
    await expect(inspector.getByTestId("point-value-1")).toContainText("dBZ");
    await inspector.getByRole("button", { name: "Open HRRR sounding here" }).click();
    await expect(page.getByTestId("workspace-panel")).toHaveAttribute("data-workspace", "sounding");
    await expect(page.getByLabel("Latitude, degrees north")).toHaveValue(/^3[56]\.\d{4}$/u);
    await expect(page.getByLabel("Longitude, degrees east")).toHaveValue(/^-10[78]\.\d{4}$/u);
    const latitude = Number(await page.getByLabel("Latitude, degrees north").inputValue());
    const longitude = Number(await page.getByLabel("Longitude, degrees east").inputValue());
    expect(Math.abs(latitude - 36)).toBeLessThan(0.05);
    expect(Math.abs(longitude + 108)).toBeLessThan(0.05);
    await expect(page.getByRole("combobox", { name: "Model" }).last()).toHaveAttribute("value", "hrrr");
    // Back on the map, Esc clears the point as it clears a station.
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer" }).click();
    await expect(inspector).toContainText("Forecast point");
    await page.keyboard.press("Escape");
    await expect(inspector).toHaveCount(0);
    expectWarnings(consoleMessages, []);
  });

  test("two and four synchronized panels, each with its own product and layers", async ({ page, consoleMessages }) => {
    await presetPreferences(page, { layers: { "station-labels": { visible: true, opacity: 1 } } });
    await page.goto("/");
    await waitForMapDrawn(page);
    const mapToolbar = page.getByRole("toolbar", { name: "Map" });
    await mapToolbar.getByRole("button", { name: "Zoom in" }).click();
    await expect.poll(async () => (await labels(page)).tier).toBe("id");
    await mapToolbar.getByRole("button", { name: "Four synchronized panels" }).click();
    for (const n of [1, 2, 3, 4]) {
      await expect(page.getByTestId(`map-panel-${String(n)}`).locator("canvas").first()).toBeVisible();
    }
    await expect(page.locator(".maplibregl-ctrl-attrib:visible")).toHaveCount(1);
    await expect(page.getByTestId("map-panel-4").locator(".maplibregl-ctrl-attrib")).toBeVisible();
    // Panels 2 to 4 open with their field on, each a different kind of product.
    for (const n of [2, 3, 4]) {
      await expect(page.getByTestId(`panel-chip-${String(n)}`)).toContainText(`${String(n)} • `);
      await expect(page.getByTestId(`panel-chip-${String(n)}`)).not.toContainText("Loading");
    }
    // A narrow panel labels as the single map would at this zoom: ids, not crowded names.
    await nextFrames(page);
    expect((await labels(page)).tier).toBe("id");
    const names = await Promise.all([2, 3, 4].map((n) => page.getByTestId(`panel-chip-${String(n)}`).locator("span").first().innerText()));
    expect(new Set(names).size).toBe(3);
    await page.getByTestId("panel-chip-3").click();
    await expect(page.getByRole("dialog", { name: "Parameters", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Layers", exact: true }).click();
    await expect(page.getByRole("region", { name: "Layers, panel 3" })).toBeVisible();
    await expect(page.getByTestId("panel-chip-3")).toHaveAttribute("aria-pressed", "true");
    // The shared time moves every panel.
    await page.getByRole("region", { name: "Forecast timeline" }).getByRole("button", { name: "Next frame" }).click();
    for (const n of [2, 3, 4]) {
      await page.getByRole("group", { name: "Active map panel" }).getByRole("button", { name: `Panel ${String(n)}`, exact: true }).click();
      const row = await layerRow(page, "model-field");
      await expect(row.getByTestId("field-shown")).toContainText("F001 of the 10-04 16Z run, valid 10-04 17Z");
    }
    await nextFrames(page);
    await saveScreenshot(page, "browser-forecast-four-panels.png");
    await mapToolbar.getByRole("button", { name: "Two synchronized panels" }).click();
    await expect(page.getByTestId("map-panel-3")).toHaveCount(0);
    await expect(page.getByTestId("map-panel-2").locator(".maplibregl-ctrl-attrib")).toBeVisible();
    // Panel 3 is gone, so the layer panel edits panel 1 again.
    await expect(page.getByRole("region", { name: "Layers, panel 1", exact: true })).toBeVisible();
    await mapToolbar.getByRole("button", { name: "One map" }).click();
    await expect(page.getByTestId("map-panel-2")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Layers", exact: true })).toBeVisible();
    expectWarnings(consoleMessages, []);
  });

  test("stations marked against the UWyo list of a release, the extra ones added", async ({ page, consoleMessages }) => {
    await page.goto("/");
    await waitForMapDrawn(page);
    await expect(page.getByTestId("catalogue-announcement")).toHaveText("934 stations loaded");
    const row = await layerRow(page, "stations");
    const select = row.getByRole("combobox", { name: "Availability" });
    await select.click();
    const option = page.getByRole("listbox", { name: "Availability" }).getByRole("option").nth(1);
    const release = await option.getAttribute("value");
    expect(release).not.toBeNull();
    await option.click();
    await expect(row.getByTestId("availability-shown")).toContainText("UWyo lists 2 bundled stations and 1 others");
    await expect(page.getByRole("list", { name: "Legend" })).toContainText("Station, not reported");
    await select.click();
    await page.getByRole("option", { name: "Automatic · observed launch", exact: true }).click();
    await expect(page.getByTestId("catalogue-announcement")).toHaveText("934 stations loaded");
    expectWarnings(consoleMessages, []);
  });
});

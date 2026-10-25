// Real-backend forecast map checks, over CDP to the real debug app and through
// the UI only (the fixture test hooks are not in desktop builds): an HRRR
// field frame from sharpmod (newest run first, stepping back while a run is not
// published), its pointer value, a second product, the next hour and a
// prefetched one, SPC outlooks, the radar mosaic, station availability, a
// forecast point, and four loaded panels with pan/playback frame times. Radar
// is checked in Live Tracking. Timings and the texts read go to
// SHARPPY_MEASURE_OUT; screenshots to E2E_SCREENSHOT_DIR. Network-dependent:
// run by hand, @map.
import { expect, test } from "@playwright/test";
import { compactUtc } from "../../src/features/sounding/soundingTime";
import {
  FIELD_MS, OVERLAY_MS, RUN_ATTEMPTS, FOUR_PRODUCTS, page, results, text, row,
  shot, seconds, openLayers, setLayer, chooseProduct, forecastIdentity, fieldOutcome,
  loadedField, mapCentre, pointerValue, loadedPanels, weatherPixels,
  measureFrames, setupMapHarness,
} from "./mapSupport";

test.describe.configure({ mode: "serial" });

test.describe("real-backend forecast map", () => {
  setupMapHarness();

  test("an HRRR field from sharpmod, its value, another product, and the next hours @map", async () => {
    test.setTimeout(RUN_ATTEMPTS * FIELD_MS + 4 * FIELD_MS);
    await page.getByRole("navigation", { name: "Workspaces" }).getByRole("button", { name: "Model Viewer", exact: true }).click();
    await page.waitForFunction(() => performance.getEntriesByName("sharppy:stations-rendered").length > 0, undefined, {
      timeout: 60_000,
    });
    await expect(page.getByTestId("catalogue-announcement")).toHaveText(/^[1-9]\d* stations loaded$/u);
    await page.getByRole("toolbar", { name: "Map" }).getByRole("button", { name: "One map", exact: true }).click();
    const toolbar = page.getByRole("group", { name: "Forecast", exact: true });
    const run = toolbar.getByRole("button", { name: "Choose run" });
    await toolbar.getByRole("button", { name: "Choose model", exact: true }).click();
    await page.getByRole("dialog", { name: "Forecast model", exact: true }).getByRole("button", { name: "Choose HRRR", exact: true }).click();
    await run.click();
    await page.getByRole("dialog", { name: "Model run", exact: true }).getByRole("button", { name: "Use latest suggested run", exact: true }).click();
    await toolbar.getByRole("button", { name: "Choose forecast hour", exact: true }).click();
    await page.getByRole("dialog", { name: "Forecast hour", exact: true }).getByRole("button", { name: "Select forecast hour 0", exact: true }).click();
    await chooseProduct("Composite Reflectivity", "dBZ");
    await expect(toolbar.getByRole("button", { name: "Choose model" })).toContainText("HRRR");
    results.forecastFields = {
      run: await run.innerText(),
      hours: Number(await page.getByRole("slider", { name: "Forecast hour" }).getAttribute("max")) + 1,
      valid: await text(page.getByLabel("Valid time")),
    };
    await setLayer("model-field", true);
    await row("model-field").getByRole("slider", { name: "Model field opacity", exact: true }).press("End");
    await page.getByRole("group", { name: "Layer categories" }).getByRole("button", { name: "Map details", exact: true }).click();
    await page.getByRole("group", { name: "Map projection" }).getByRole("button", { name: "Flat map", exact: true }).click();
    await page.getByRole("group", { name: "Layer categories" }).getByRole("button", { name: "Weather", exact: true }).click();
    await page.getByRole("button", { name: "Reset view", exact: true }).click();
    await page.waitForTimeout(1_000);
    const attempts: unknown[] = [];
    results.fieldAttempts = attempts;
    let outcome = { ok: false, seconds: 0, text: "" };
    for (let attempt = 0; attempt < RUN_ATTEMPTS && !outcome.ok; attempt += 1) {
      if (attempt > 0) {
        // An older run, as the newest may not be published yet.
        await run.click();
        const setup = page.getByRole("dialog", { name: "Model run" });
        const runs = setup.getByRole("button", { name: /^Run \d\d-\d\d \d\dZ$/u });
        const states = await runs.evaluateAll((items) => items.map((item) => item.getAttribute("aria-pressed")));
        const chosen = states.indexOf("true");
        expect(chosen).toBeGreaterThanOrEqual(0);
        if (chosen === 0) {
          await setup.getByRole("button", { name: "Previous run date" }).click();
          await runs.last().click();
        } else {
          await runs.nth(chosen - 1).click();
        }
      }
      outcome = await fieldOutcome();
      attempts.push({ run: await run.innerText(), ...outcome });
      if (!outcome.ok) await shot(`desktop-map-field-attempt-${String(attempt + 1)}.png`, true);
    }
    expect(outcome.ok, `a field frame loaded: ${JSON.stringify(attempts)}`).toBe(true);
    const scale = page.getByRole("complementary", { name: "Parameter scale" });
    await expect(scale).toHaveAccessibleDescription(/dBZ/u);
    await expect(page.getByTestId("panel-chip-1")).toHaveCount(0);
    results.scale = { description: await scale.getAttribute("aria-description"), value: await scale.getByRole("slider").getAttribute("aria-valuetext") };
    await page.getByRole("button", { name: "Close layers", exact: true }).click();
    results.value = await pointerValue(1, "Composite Reflectivity", "dBZ");
    await shot("desktop-map-field.png");

    await chooseProduct("2 m AGL Temperature", "°F");
    await setLayer("model-field", true);
    results.secondProduct = await loadedField();
    await expect(scale).toHaveAccessibleDescription(/°F/u);
    results.secondScale = { description: await scale.getAttribute("aria-description"), value: await scale.getByRole("slider").getAttribute("aria-valuetext") };
    await page.getByRole("button", { name: "Close layers", exact: true }).click();
    results.secondValue = await pointerValue(1, "2 m AGL Temperature", "°F");

    const timeline = page.getByRole("region", { name: "Forecast timeline" });
    const next = timeline.getByRole("button", { name: "Next frame" });
    const hour = page.getByRole("slider", { name: "Forecast hour", exact: true });
    await setLayer("model-field", true);
    await next.click();
    await expect(hour).toHaveValue("1");
    const nextHour = await loadedField();
    results.nextHour = nextHour;
    // The frame after it was prefetched while this one showed.
    await page.waitForTimeout(15_000);
    await next.click();
    await expect(hour).toHaveValue("2");
    const prefetchedHour = await loadedField();
    expect(prefetchedHour.forecast.run).toBe(nextHour.forecast.run);
    expect(prefetchedHour.forecast.valid).not.toBe(nextHour.forecast.valid);
    results.prefetchedHour = prefetchedHour;
    results.timeline = await text(timeline);
    await page.getByRole("button", { name: "Close layers", exact: true }).click();
    await shot("desktop-map-field-next.png");
  });

  test("SPC outlooks and station availability in Model Viewer @map", async () => {
    test.setTimeout(4 * OVERLAY_MS);
    await setLayer("model-field", false);
    await setLayer("spc-cat", true);
    const started = Date.now();
    const cat = page.getByTestId("outlook-shown-cat");
    await expect(cat).toBeVisible({ timeout: OVERLAY_MS });
    await expect(cat).toContainText("NOAA/NWS Storm Prediction Center");
    await expect(cat).not.toContainText("No SPC outlook covers");
    const categorical = { seconds: seconds(started), text: await text(cat) };
    results.outlookCat = categorical;
    const outlookTime = await forecastIdentity();
    await setLayer("spc-torn", true);
    const torn = page.getByTestId("outlook-shown-torn");
    const none = page.getByTestId("outlook-none-torn");
    const error = page.getByTestId("outlook-error-torn");
    await expect.poll(async () => await torn.isVisible() || await none.isVisible() || await error.isVisible(), {
      timeout: OVERLAY_MS, intervals: [250],
    }).toBe(true);
    await expect(error).toHaveCount(0);
    expect(await forecastIdentity()).toEqual(outlookTime);
    // Quiet SPC products contain no drawable areas; the bridge returns outlook: null.
    if (await none.isVisible()) {
      await expect(none).toHaveText(`SPC published no outlook of this kind for ${outlookTime.valid}.`);
      await expect(cat).toHaveText(categorical.text);
    } else {
      await expect(torn).toContainText("NOAA/NWS Storm Prediction Center");
      await expect(torn).not.toContainText("No SPC outlook covers");
    }
    results.outlookTorn = { state: await none.isVisible() ? "no-areas" : "shown", text: await text(await none.isVisible() ? none : torn),
      forecast: outlookTime, categorical: results.outlookCat };

    await shot("desktop-map-overlays.png");

    await setLayer("stations", true);
    const select = row("stations").getByRole("combobox", { name: "Availability" });
    await select.click();
    const option = page.getByRole("listbox", { name: "Availability" }).getByRole("option").nth(1);
    const release = await option.getAttribute("value");
    expect(release).toMatch(/^\d{4}-\d\d-\d\dT(?:00|12):00:00Z$/u);
    await option.click();
    const availabilityStarted = Date.now();
    const status = row("stations").getByTestId("availability-shown");
    await expect(status).toBeVisible({ timeout: OVERLAY_MS });
    await expect(status).toContainText(`for ${compactUtc(release ?? "")}.`);
    await expect(status).toContainText(/UWyo lists [1-9]\d* bundled stations/u);
    results.availability = {
      release,
      seconds: seconds(availabilityStarted),
      text: await text(status),
      count: await text(page.getByTestId("catalogue-announcement")),
    };
    for (const id of ["spc-cat", "spc-torn"]) {
      await setLayer(id, false);
    }
    await setLayer("stations", true);
    await select.click();
    await page.getByRole("option", { name: "Automatic · observed launch", exact: true }).click();
  });

  test("the real MRMS radar mosaic in Live Tracking @map", async () => {
    test.setTimeout(2 * OVERLAY_MS);
    const rail = page.getByRole("navigation", { name: "Workspaces" });
    await rail.getByRole("button", { name: "Live Tracking", exact: true }).click();
    const controls = page.getByRole("group", { name: "Live radar", exact: true });
    await controls.getByRole("combobox", { name: "Site", exact: true }).click();
    await page.getByRole("listbox", { name: "Site", exact: true }).getByRole("option", { name: "CONUS mosaic", exact: true }).click();
    await controls.getByRole("combobox", { name: "Site Product", exact: true }).click();
    await page.getByRole("listbox", { name: "Site Product", exact: true }).getByRole("option", { name: "Composite Reflectivity", exact: true }).click();
    await page.getByRole("region", { name: "Live Tracking timeline", exact: true }).getByRole("button", { name: "Live", exact: true }).click();
    await setLayer("radar", true);
    const started = Date.now();
    const radar = row("radar").getByTestId("radar-shown");
    await expect(radar).toBeVisible({ timeout: OVERLAY_MS });
    await expect(radar).toContainText("High-resolution MRMS mosaic.");
    await expect(radar).toContainText(/valid \d{4}-\d\d-\d\d/u);
    const scale = page.getByRole("complementary", { name: "Radar scale", exact: true });
    await expect(scale).toHaveAccessibleDescription(/dBZ · Panel 1 · Scan \d{4}-\d\d-\d\d/u);
    results.radar = { seconds: seconds(started), text: await text(radar), scale: await scale.getAttribute("aria-description"),
      viewing: await text(page.getByLabel("Live viewing time", { exact: true })) };
    await page.getByRole("button", { name: "Close layers", exact: true }).click();
    await shot("desktop-live-radar.png");
    await rail.getByRole("button", { name: "Model Viewer", exact: true }).click();
  });

  test("a forecast point, four loaded weather panels, and pan/playback frame times @map", async () => {
    test.setTimeout(8 * FIELD_MS);
    await setLayer("stations", false);
    await setLayer("model-field", true);
    const pointField = await loadedField();
    await page.getByRole("button", { name: "Close layers", exact: true }).click();
    const centre = await mapCentre();
    await page.mouse.click(centre.x, centre.y);
    const inspector = page.getByRole("complementary", { name: "Inspector" });
    await expect(inspector).toContainText("Forecast point");
    await expect(inspector.getByTestId("point-value-1")).toHaveText(/^-?\d+(?:\.\d+)? °F$/u);
    await expect(inspector.getByTestId("point-forecast")).toContainText(`HRRR ${pointField.forecast.hour}`);
    await expect(inspector.getByTestId("point-forecast")).toContainText(`Valid ${pointField.forecast.valid} UTC`);
    results.point = { text: await text(inspector), forecast: pointField.forecast };
    await page.getByRole("button", { name: "Close map details", exact: true }).click();

    const started = Date.now();
    await page.getByRole("toolbar", { name: "Map" }).getByRole("button", { name: "Four synchronized panels" }).click();
    await expect(page.locator('[data-testid^="map-panel-"]')).toHaveCount(4);
    await openLayers();
    for (const [index, product] of FOUR_PRODUCTS.entries()) {
      await page.getByRole("group", { name: "Active map panel" }).getByRole("button", { name: `Panel ${String(index + 1)}`, exact: true }).click();
      await chooseProduct(product.label, product.unit);
      await setLayer("model-field", true);
      await row("model-field").getByRole("slider", { name: "Model field opacity", exact: true }).press("End");
      await loadedField();
    }
    const panels = await loadedPanels();
    const weather = [];
    for (const [index, product] of FOUR_PRODUCTS.entries()) {
      const panel = index + 1;
      weather.push({ panel, value: await pointerValue(panel, product.label, product.unit), pixels: await weatherPixels(panel) });
    }
    // Re-enabling a layer above must still expose the same current frame in every panel.
    const ready = await loadedPanels();
    results.fourPanels = { seconds: seconds(started), panels, weather, ready };
    await page.mouse.move(0, 0);
    await shot("desktop-map-four-panels.png");

    const panCentre = await mapCentre();
    results.loadedWeatherPan = { forecast: await forecastIdentity(), panels: ready, frames: await measureFrames(async () => {
      await page.mouse.move(panCentre.x - 120, panCentre.y);
      await page.mouse.down();
      try {
        for (let step = 1; step <= 60; step += 1) {
          await page.mouse.move(panCentre.x - 120 + step * 4, panCentre.y);
          await page.waitForTimeout(16);
        }
      } finally {
        await page.mouse.up();
      }
      await page.waitForTimeout(300);
    }) };
    await page.getByRole("button", { name: "Reset view", exact: true }).click();
    await page.waitForTimeout(1_000);
    const before = await loadedPanels();
    const timeline = page.getByRole("region", { name: "Forecast timeline", exact: true });
    // One half speed gives the real four-panel draw a 1.5 s display interval.
    const speed = timeline.getByRole("button", { name: /^Playback speed /u });
    for (let attempt = 0; attempt < 4 && await speed.getAttribute("aria-label") !== "Playback speed 0.5 times"; attempt += 1) await speed.click();
    await expect(speed).toHaveAccessibleName("Playback speed 0.5 times");
    const from = await forecastIdentity();
    const hour = page.getByRole("slider", { name: "Forecast hour", exact: true });
    const fromIndex = Number(await hour.inputValue());
    const playback = await measureFrames(async () => {
      await timeline.getByRole("button", { name: "Play", exact: true }).click();
      try {
        // Poll the real timeline; no synthetic clock and no basemap-only playback.
        await expect.poll(async () => Number(await hour.inputValue()), { timeout: 2 * FIELD_MS, intervals: [100] }).toBeGreaterThan(fromIndex);
      } finally {
        await timeline.getByRole("button", { name: "Pause", exact: true }).click();
      }
      await page.waitForTimeout(300);
    });
    const after = await loadedPanels();
    const to = await forecastIdentity();
    expect(to.run).toBe(from.run);
    expect(to.hour).not.toBe(from.hour);
    expect(to.valid).not.toBe(from.valid);
    const drawnAfter = [];
    for (const [index, product] of FOUR_PRODUCTS.entries()) {
      drawnAfter.push({ panel: index + 1, value: await pointerValue(index + 1, product.label, product.unit), pixels: await weatherPixels(index + 1) });
    }
    results.loadedWeatherPlayback = { from, to, before, after, weather: drawnAfter, frames: playback };
    const stopped = await hour.inputValue();
    await page.waitForTimeout(1_600);
    await expect(hour).toHaveValue(stopped);
    await page.getByRole("group", { name: "Map detail views" }).getByRole("button", { name: "Point details", exact: true }).click();
    for (const [index, product] of FOUR_PRODUCTS.entries()) {
      await expect(inspector.getByTestId(`point-value-${String(index + 1)}`)).toHaveText(/^-?\d+(?:\.\d+)? /u);
      await expect(inspector.getByTestId(`point-value-${String(index + 1)}`)).toContainText(product.unit);
    }
    await expect(inspector.getByTestId("point-forecast")).toContainText(`HRRR ${to.hour}`);
    await expect(inspector.getByTestId("point-forecast")).toContainText(`Valid ${to.valid} UTC`);
    results.fourPanelPoint = await text(inspector);
    await page.mouse.move(0, 0);
    await shot("desktop-map-four-panels-playback.png");
    await inspector.getByRole("button", { name: "Clear selection", exact: true }).click();
    await page.getByRole("toolbar", { name: "Map" }).getByRole("button", { name: "One map" }).click();
  });
});

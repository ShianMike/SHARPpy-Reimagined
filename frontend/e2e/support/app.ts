// Helpers over the app's User Timing marks and the fixture-only test hooks
// (window.__SHARPPY_E2E__, DESIGN-frontend F4).
import type { Page } from "@playwright/test";

export const MARKS = {
  shellPainted: "sharppy:shell-painted",
  mapLoaded: "sharppy:map-loaded",
  basemapLoaded: "sharppy:basemap-loaded",
  catalogReceived: "sharppy:catalog-received",
  stationsRendered: "sharppy:stations-rendered",
} as const;

interface ProjectedPoint {
  x: number;
  y: number;
}

interface E2eHooks {
  projectStation: (id: string) => ProjectedPoint | null;
  labelCount: () => number;
  labelTier: () => string | null;
  camera: () => { center: [number, number]; zoom: number } | null;
  projection: () => string | null;
  profile: () => { mode: string; profile: string; reasons: string[] };
  basemapFeatureCounts: () => Record<string, number>;
  mapErrors: () => string[];
  deckLayers: () => string[];
  projectPoint: (longitude: number, latitude: number) => ProjectedPoint | null;
}

declare global {
  interface Window {
    __SHARPPY_E2E__?: E2eHooks;
  }
}

/** Waits for a User Timing mark; the map marks come from the lazy map chunk. */
export async function waitForMark(page: Page, name: string, timeout = 30_000): Promise<void> {
  await page.waitForFunction((mark) => performance.getEntriesByName(mark, "mark").length > 0, name, { timeout });
}

/** Waits until the fixture build has installed its hooks. */
export async function waitForHooks(page: Page): Promise<void> {
  await page.waitForFunction(() => window.__SHARPPY_E2E__ !== undefined);
}

export async function projectStation(page: Page, id: string): Promise<ProjectedPoint | null> {
  await waitForHooks(page);
  return page.evaluate((stationId) => window.__SHARPPY_E2E__?.projectStation(stationId) ?? null, id);
}

export async function basemapFeatureCounts(page: Page): Promise<Record<string, number>> {
  await waitForHooks(page);
  return page.evaluate(() => window.__SHARPPY_E2E__?.basemapFeatureCounts() ?? {});
}

/** The deck.gl layers the primary map last drew visible, bottom first. */
export async function deckLayers(page: Page): Promise<string[]> {
  await waitForHooks(page);
  return page.evaluate(() => window.__SHARPPY_E2E__?.deckLayers() ?? []);
}
export async function projectPoint(page: Page, longitude: number, latitude: number): Promise<ProjectedPoint | null> {
  await waitForHooks(page);
  return page.evaluate(([lon, lat]) => window.__SHARPPY_E2E__?.projectPoint(lon, lat) ?? null, [longitude, latitude] as const);
}
export async function mapErrors(page: Page): Promise<string[]> {
  await waitForHooks(page);
  return page.evaluate(() => window.__SHARPPY_E2E__?.mapErrors() ?? []);
}

export async function effectiveProfile(page: Page): Promise<{ mode: string; profile: string; reasons: string[] } | null> {
  await waitForHooks(page);
  return page.evaluate(() => window.__SHARPPY_E2E__?.profile() ?? null);
}
export async function labels(page: Page): Promise<{ count: number; tier: string | null }> {
  await waitForHooks(page);
  return page.evaluate(() => ({
    count: window.__SHARPPY_E2E__?.labelCount() ?? 0,
    tier: window.__SHARPPY_E2E__?.labelTier() ?? null,
  }));
}

export async function camera(page: Page): Promise<{ center: [number, number]; zoom: number } | null> {
  await waitForHooks(page);
  return page.evaluate(() => window.__SHARPPY_E2E__?.camera() ?? null);
}

/** Stored preferences written before the app boots (sharppy.prefs.v1). */
export async function presetPreferences(page: Page, preferences: Record<string, unknown>): Promise<void> {
  await page.addInitScript((value) => {
    window.localStorage.setItem("sharppy.prefs.v1", JSON.stringify({ version: 1, ...value }));
  }, preferences);
}

/** The app with getContext("webgl2") returning null, as on a device without WebGL2. */
export async function stubNoWebgl2(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type GetContext = (this: HTMLCanvasElement, id: string, options?: unknown) => RenderingContext | null;
    // eslint-disable-next-line @typescript-eslint/unbound-method -- called below with .call(this)
    const original = HTMLCanvasElement.prototype.getContext as GetContext;
    const patched: GetContext = function getContext(id, options) {
      return id === "webgl2" ? null : original.call(this, id, options);
    };
    HTMLCanvasElement.prototype.getContext = patched as typeof HTMLCanvasElement.prototype.getContext;
  });
}

/** Stations and all four basemap sources drawn: the state a map screenshot should show. */
export async function waitForMapDrawn(page: Page): Promise<void> {
  // Playwright's clock returns no User Timing entries; inspect the rendered map instead.
  await page.waitForFunction(() => {
    const hooks = window.__SHARPPY_E2E__;
    const counts = Object.values(hooks?.basemapFeatureCounts() ?? {});
    return hooks?.deckLayers().includes("stations") && counts.length === 4 && counts.every((count) => count > 0);
  });
  await nextFrames(page);
}

/** Resolves after two animation frames, so a store change has been drawn. */
export async function nextFrames(page: Page): Promise<void> {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            resolve();
          });
        });
      }),
  );
}

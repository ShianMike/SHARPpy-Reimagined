// Pixel checks on screenshots, decoded in the page: the map canvas keeps
// preserveDrawingBuffer off (DESIGN-frontend F9 rule 7), so tests read pixels
// from Playwright screenshots instead of the WebGL context.
import type { Page } from "@playwright/test";

export interface RegionStats {
  width: number;
  height: number;
  /** Mean luminance, 0 to 255. */
  mean: number;
  /** Standard deviation of luminance; 0 for a uniform region. */
  stdDev: number;
  /** Distinct RGB colours. */
  distinct: number;
}

/** Luminance statistics of a PNG screenshot. */
export async function regionStats(page: Page, png: Buffer): Promise<RegionStats> {
  return page.evaluate(async (base64) => {
    const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (context === null) {
      throw new Error("no 2D context to decode the screenshot");
    }
    context.drawImage(bitmap, 0, 0);
    const { data } = context.getImageData(0, 0, bitmap.width, bitmap.height);
    let sum = 0;
    let sumSquares = 0;
    const colours = new Set<number>();
    const count = data.length / 4;
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i] ?? 0;
      const g = data[i + 1] ?? 0;
      const b = data[i + 2] ?? 0;
      const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
      sum += y;
      sumSquares += y * y;
      colours.add((r << 16) | (g << 8) | b);
    }
    const mean = sum / count;
    return {
      width: bitmap.width,
      height: bitmap.height,
      mean,
      stdDev: Math.sqrt(Math.max(0, sumSquares / count - mean * mean)),
      distinct: colours.size,
    };
  }, png.toString("base64"));
}

/** The RGBA colour of one pixel of a PNG screenshot. */
export async function pixelAt(page: Page, png: Buffer, x: number, y: number): Promise<[number, number, number, number]> {
  return page.evaluate(
    async ({ base64, px, py }) => {
      const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext("2d");
      if (context === null) {
        throw new Error("no 2D context to decode the screenshot");
      }
      context.drawImage(bitmap, 0, 0);
      const [r = 0, g = 0, b = 0, a = 0] = context.getImageData(px, py, 1, 1).data;
      return [r, g, b, a] as [number, number, number, number];
    },
    { base64: png.toString("base64"), px: x, py: y },
  );
}

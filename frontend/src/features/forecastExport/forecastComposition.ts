import type { FieldFrame, FieldProduct } from "../../types/map";
import { convertUnit, displayUnit, type UnitPreferences } from "../../lib/displayUnits";
import { buildLut, LUT_SIZE, paletteRange } from "../../map/field/colourize";
import { compactUtc, forecastLabel } from "../sounding/soundingTime";

export interface CapturedForecast {
  map: HTMLCanvasElement | ImageBitmap;
  frame: Pick<FieldFrame, "forecastHour" | "validTime" | "attribution"> & { basemapAttribution?: string };
}
export interface ForecastComposition {
  width: number; height: number; model: string; run: string; product: FieldProduct; units: UnitPreferences;
}

/** The preview and GIF share this compositor, including the entire range and source/time labels. */
export function composeForecast(canvas: HTMLCanvasElement, captured: CapturedForecast, options: ForecastComposition): void {
  const { width, height, product } = options;
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (context === null) throw new Error("Image export is unavailable.");
  const theme = getComputedStyle(document.documentElement);
  const scale = width / 1280, padding = 20 * scale, font = 16 * scale;
  const background = theme.getPropertyValue("--surface-1").trim();
  const foreground = theme.getPropertyValue("--text-primary").trim();
  const muted = theme.getPropertyValue("--text-secondary").trim();
  const family = theme.getPropertyValue("--font-sans").trim() || "sans-serif";
  context.fillStyle = background;
  context.fillRect(0, 0, width, height);
  // Fill the entire plot without stretching geography or enlarging a low-resolution screen copy.
  const fit = Math.max(width / captured.map.width, height / captured.map.height);
  const cropWidth = width / fit, cropHeight = height / fit;
  context.imageSmoothingEnabled = true;
  context.drawImage(captured.map, (captured.map.width - cropWidth) / 2, (captured.map.height - cropHeight) / 2,
    cropWidth, cropHeight, 0, 0, width, height);
  context.globalAlpha = 0.9;
  context.fillRect(0, 0, width, 74 * scale);
  context.fillRect(0, height - 22 * scale, width, 22 * scale);
  context.globalAlpha = 1;
  context.textBaseline = "top";
  context.fillStyle = foreground;
  context.font = `600 ${String(font * 1.25)}px ${family}`;
  context.fillText(`${options.model} · ${product.label}`, padding, padding, width * 0.72);
  context.font = `${String(font * 0.8)}px ${family}`;
  context.fillStyle = muted;
  context.fillText(`Run ${compactUtc(options.run)} UTC`, padding, padding + font * 1.9);
  context.textAlign = "right";
  context.fillStyle = foreground;
  context.font = `600 ${String(font)}px ${family}`;
  context.fillText(`${forecastLabel(captured.frame.forecastHour)} · ${compactUtc(captured.frame.validTime)} UTC`, width - padding, padding + font * 2);
  context.fillStyle = muted;
  context.font = `${String(font * 0.8)}px ${family}`;
  context.fillText("SHARPpy Reimagined", width - padding, padding);
  context.textAlign = "left";

  const top = 100 * scale, bottom = 60 * scale, legendWidth = 90 * scale;
  const mapHeight = height - top - bottom;

  const ramp = document.createElement("canvas");
  ramp.width = 1; ramp.height = LUT_SIZE;
  const pixels = new Uint8ClampedArray(buildLut(product.palette));
  new Uint32Array(pixels.buffer).reverse();
  ramp.getContext("2d")?.putImageData(new ImageData(pixels, 1, LUT_SIZE), 0, 0);
  const rampLeft = width - padding - 14 * scale, rampTop = top + font * 2, rampHeight = mapHeight - font * 4;
  context.imageSmoothingEnabled = false;
  context.drawImage(ramp, rampLeft, rampTop, 12 * scale, rampHeight);
  context.strokeStyle = muted;
  context.lineWidth = scale;
  context.strokeRect(rampLeft, rampTop, 12 * scale, rampHeight);
  const unit = displayUnit(product.palette.units, options.units, product.palette.display);
  context.fillStyle = foreground;
  context.font = `${String(font * 0.85)}px ${family}`;
  context.textAlign = "right";
  context.strokeStyle = background;
  context.lineWidth = 3 * scale;
  context.lineJoin = "round";
  context.strokeText(unit || (product.palette.units === "flag" ? "Present" : "Unitless"), width - padding, top, legendWidth);
  context.fillText(unit || (product.palette.units === "flag" ? "Present" : "Unitless"), width - padding, top, legendWidth);
  const { low, high } = paletteRange(product.palette);
  context.textBaseline = "middle";
  for (const fraction of product.palette.units === "flag" ? [1, 0] : [1, 0.75, 0.5, 0.25, 0]) {
    const value = convertUnit(low + fraction * (high - low), product.palette.units, unit, product.palette.display);
    const text = product.palette.units === "flag" ? fraction === 1 ? "Present" : "Absent" : String(Math.round(value));
    context.strokeText(text, rampLeft - 8 * scale, rampTop + (1 - fraction) * rampHeight);
    context.fillText(text, rampLeft - 8 * scale, rampTop + (1 - fraction) * rampHeight);
  }
  context.textAlign = "left";
  context.textBaseline = "bottom";
  context.fillStyle = muted;
  context.font = `${String(font * 0.55)}px ${family}`;
  context.fillText(`${captured.frame.attribution} · ${captured.frame.basemapAttribution ?? "Natural Earth"}`, padding, height - 8 * scale, width - padding * 2);
}

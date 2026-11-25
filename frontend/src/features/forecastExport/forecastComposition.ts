import { version } from "../../../package.json";
import type { FieldFrame, FieldProduct, Palette } from "../../types/map";
import { convertUnit, displayUnit, type UnitPreferences } from "../../lib/displayUnits";
import { buildLut, formatScaleTick, LUT_SIZE, paletteRange, ptypePaletteGroups, ptypePaletteTicks } from "../../map/field/colourize";
import { PRECIPITATION_ICON_PATHS } from "../../map/field/precipitationIcons";
import { compactUtc, forecastLabel } from "../sounding/soundingTime";

let precipitationIconPaths: readonly Path2D[] | undefined;

export interface CapturedForecast {
  map: HTMLCanvasElement | ImageBitmap;
  frame: Pick<FieldFrame, "forecastHour" | "validTime" | "attribution"> & { basemapAttribution?: string };
}
export interface ForecastComposition {
  width: number; height: number; model: string; run: string; product: FieldProduct; units: UnitPreferences;
}

interface LegendRamp {
  left: number;
  top: number;
  width: number;
  height: number;
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
  context.fillText(`SHARPpy Reimagined v${version}`, width - padding, padding);
  context.textAlign = "left";

  const { palette } = product;
  const ptypeGroups = ptypePaletteGroups(palette);
  const top = 100 * scale, bottom = 60 * scale;
  const columnWidth = 12 * scale, gap = 4 * scale;
  const rampRight = width - padding - 2 * scale;
  if (ptypeGroups !== null && palette.units === "in") {
    const paths = precipitationIconPaths ??= PRECIPITATION_ICON_PATHS.map((path) => new Path2D(path));
    const iconSize = 20 * scale;
    const iconScale = iconSize / 24;
    const blockGap = 12 * scale;
    const blockHeight = (height - top - bottom - (ptypeGroups.length - 1) * blockGap) / ptypeGroups.length;
    const rampHeight = blockHeight - iconSize - gap;
    const left = rampRight - columnWidth;
    const iconLeft = left + (columnWidth - iconSize) / 2;
    context.font = `${String(Math.max(8, font * 0.85))}px ${family}`;
    context.textAlign = "right";
    context.textBaseline = "middle";
    context.lineJoin = "round";
    ptypeGroups.forEach(({ stops }, index) => {
      const iconTop = top + index * (blockHeight + blockGap);
      const path = paths[index];
      if (path !== undefined) {
        context.save();
        context.translate(iconLeft, iconTop);
        context.scale(iconScale, iconScale);
        context.lineCap = "round";
        // Stroke the outline first for contrast over the map.
        context.strokeStyle = background;
        context.lineWidth = 5.5;
        context.stroke(path);
        context.strokeStyle = foreground;
        context.lineWidth = 2.4;
        context.stroke(path);
        context.restore();
      }
      const ramp = { left, top: iconTop + iconSize + gap, width: columnWidth, height: rampHeight };
      context.strokeStyle = muted;
      context.lineWidth = scale;
      drawPtypeRamp(context, stops, ramp);
      context.fillStyle = foreground;
      context.strokeStyle = background;
      context.lineWidth = 3 * scale;
      drawPtypeTicks(context, palette.ticks, stops, ramp, 8 * scale, 44 * scale);
    });
  } else {
    const ptypeStops = ptypeGroups?.[0]?.stops;
    const legendWidth = (ptypeGroups === null ? 90 : 100) * scale;
    const rampWidth = ptypeGroups === null ? columnWidth : ptypeGroups.length * (columnWidth + gap) - gap;
    const rampLeft = rampRight - rampWidth, rampTop = top + font * 2;
    const availableHeight = height - top - bottom - font * 4;
    context.font = `${String(font * 0.85)}px ${family}`;
    const labelHeight = ptypeGroups === null ? 0 : Math.min(availableHeight / 3,
      Math.max(...ptypeGroups.map(({ label }) => context.measureText(label).width)));
    const rampHeight = availableHeight - (ptypeGroups === null ? 0 : labelHeight + gap);
    const rampBounds = { left: rampLeft, top: rampTop, width: columnWidth, height: rampHeight };
    context.strokeStyle = muted;
    context.lineWidth = scale;
    if (ptypeGroups === null) {
      const ramp = document.createElement("canvas");
      ramp.width = 1; ramp.height = LUT_SIZE;
      const pixels = new Uint8ClampedArray(buildLut(palette));
      new Uint32Array(pixels.buffer).reverse();
      ramp.getContext("2d")?.putImageData(new ImageData(pixels, 1, LUT_SIZE), 0, 0);
      context.imageSmoothingEnabled = false;
      context.drawImage(ramp, rampLeft, rampTop, columnWidth, rampHeight);
      context.strokeRect(rampLeft, rampTop, columnWidth, rampHeight);
    } else {
      ptypeGroups.forEach(({ stops }, column) => {
        drawPtypeRamp(context, stops, { ...rampBounds, left: rampLeft + column * (columnWidth + gap) });
      });
    }
    const unit = displayUnit(palette.units, options.units, palette.display);
    const unitLabel = unit || (palette.units === "flag" ? "Present" : "Unitless");
    context.fillStyle = foreground;
    context.textAlign = "right";
    context.strokeStyle = background;
    context.lineWidth = 3 * scale;
    context.lineJoin = "round";
    context.strokeText(unitLabel, width - padding, top, legendWidth);
    context.fillText(unitLabel, width - padding, top, legendWidth);
    context.textBaseline = "middle";
    const tickGap = 8 * scale;
    if (ptypeGroups !== null && ptypeStops !== undefined) {
      drawPtypeTicks(context, palette.ticks, ptypeStops, rampBounds, tickGap, legendWidth - rampWidth - tickGap);
      ptypeGroups.forEach(({ label }, column) => {
        context.save();
        context.translate(rampLeft + column * (columnWidth + gap) + columnWidth / 2, rampTop + rampHeight + gap);
        context.rotate(Math.PI / 2);
        context.textAlign = "left";
        context.strokeText(label, 0, 0, labelHeight);
        context.fillText(label, 0, 0, labelHeight);
        context.restore();
      });
    } else {
      const { low, high } = paletteRange(palette);
      const span = convertUnit(high, palette.units, unit, palette.display) - convertUnit(low, palette.units, unit, palette.display);
      for (const at of palette.units === "flag" ? [1, 0] : [1, 0.75, 0.5, 0.25, 0]) {
        const value = convertUnit(low + at * (high - low), palette.units, unit, palette.display);
        const text = palette.units === "flag" ? at === 1 ? "Present" : "Absent" : formatScaleTick(value, span);
        context.strokeText(text, rampLeft - tickGap, rampTop + (1 - at) * rampHeight);
        context.fillText(text, rampLeft - tickGap, rampTop + (1 - at) * rampHeight);
      }
    }
  }
  context.textAlign = "left";
  context.textBaseline = "bottom";
  context.fillStyle = muted;
  context.font = `${String(font * 0.55)}px ${family}`;
  context.fillText(`${captured.frame.attribution} · ${captured.frame.basemapAttribution ?? "Natural Earth"}`, padding, height - 8 * scale, width - padding * 2);
}

function drawPtypeRamp(context: CanvasRenderingContext2D, stops: Palette["stops"], { left, top, width, height }: LegendRamp): void {
  stops.forEach(([, colour], index) => {
    const edge = Math.round(top + (1 - index / stops.length) * height);
    const next = Math.round(top + (1 - (index + 1) / stops.length) * height);
    context.fillStyle = colour;
    context.fillRect(left, next, width, edge - next);
  });
  context.strokeRect(left, top, width, height);
}

function drawPtypeTicks(context: CanvasRenderingContext2D, ticks: Palette["ticks"], stops: Palette["stops"],
  { left, top, height }: LegendRamp, gap: number, maxWidth: number): void {
  const high = stops.at(-1)?.[0];
  for (const { value, at } of ptypePaletteTicks(ticks, stops)) {
    const text = `${String(value)}${value === high ? "+" : ""}`;
    const y = Math.round(top + (1 - at) * height);
    context.strokeText(text, left - gap, y, maxWidth);
    context.fillText(text, left - gap, y, maxWidth);
  }
}

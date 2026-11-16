import { convertUnit, displayUnit, type UnitPreferences } from "../../lib/displayUnits";
import { buildLut, formatScaleTick, LUT_SIZE, paletteRange } from "../../map/field/colourize";
import type { SoundingMapSnapshot } from "../../map/runtime/soundingLocator";
import type { SoundingView } from "../../types/soundingView";

/** Fill the native inset and put the place name on the existing sounding header row. */
export async function composeSoundingLocator(view: SoundingView, snapshot: SoundingMapSnapshot,
  units: UnitPreferences, location: string): Promise<SoundingView> {
  const bounds = view.locatorBounds;
  if (bounds === undefined) return view;
  const sounding = await createImageBitmap(view.image);
  let map: ImageBitmap | undefined;
  try {
    map = await createImageBitmap(snapshot.image);
    const canvas = document.createElement("canvas");
    canvas.width = view.width; canvas.height = view.height;
    const ctx = canvas.getContext("2d");
    if (ctx === null) throw new Error("Sounding locator image is unavailable.");
    const theme = getComputedStyle(document.documentElement);
    const foreground = theme.getPropertyValue("--map-selected-edge").trim();
    const halo = theme.getPropertyValue("--map-label-halo").trim();
    const selected = theme.getPropertyValue("--map-selected").trim();
    ctx.drawImage(sounding, 0, 0);
    const x = bounds.left * view.width, y = bounds.top * view.height;
    const width = (bounds.right - bounds.left) * view.width, height = (bounds.bottom - bounds.top) * view.height;
    const scale = width / 500, inset = 5 * scale;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(map, x, y, width, height);

    // Marker is computed from MapLibre's actual projection, including map pitch.
    const px = x + snapshot.point.x * width, py = y + snapshot.point.y * height;
    ctx.strokeStyle = halo; ctx.lineWidth = 5 * scale;
    ctx.beginPath(); ctx.arc(px, py, 6 * scale, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = foreground; ctx.lineWidth = 2 * scale;
    ctx.beginPath(); ctx.arc(px, py, 6 * scale, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = selected; ctx.fill();

    if (snapshot.product !== null) {
      const palette = snapshot.product.palette, unit = displayUnit(palette.units, units, palette.display);
      const ramp = document.createElement("canvas");
      ramp.width = 1; ramp.height = LUT_SIZE;
      const colors = new Uint8ClampedArray(buildLut(palette));
      new Uint32Array(colors.buffer).reverse();
      ramp.getContext("2d")?.putImageData(new ImageData(colors, 1, LUT_SIZE), 0, 0);
      const rampX = x + width - 17 * scale, rampY = y + 26 * scale, rampHeight = Math.max(1, height - 43 * scale);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(ramp, rampX, rampY, 10 * scale, rampHeight);
      ctx.textAlign = "right"; ctx.textBaseline = "middle"; ctx.font = `${String(13 * scale)}px sans-serif`;
      ctx.lineJoin = "round"; ctx.lineWidth = 3 * scale; ctx.strokeStyle = halo; ctx.fillStyle = foreground;
      const { low, high } = paletteRange(palette);
      const span = convertUnit(high, palette.units, unit, palette.display) - convertUnit(low, palette.units, unit, palette.display);
      for (const fraction of [1, 0.5, 0]) {
        const value = convertUnit(low + fraction * (high - low), palette.units, unit, palette.display);
        const text = palette.units === "flag" ? fraction === 1 ? "Present" : fraction === 0 ? "Absent" : "" : formatScaleTick(value, span);
        ctx.strokeText(text, rampX - 4 * scale, rampY + (1 - fraction) * rampHeight);
        ctx.fillText(text, rampX - 4 * scale, rampY + (1 - fraction) * rampHeight);
      }
      ctx.strokeText(unit || (palette.units === "flag" ? "" : "Unitless"), x + width - inset, y + 12 * scale);
      ctx.fillText(unit || (palette.units === "flag" ? "" : "Unitless"), x + width - inset, y + 12 * scale);
    }
    ctx.strokeStyle = foreground; ctx.lineWidth = Math.max(1, scale);
    ctx.strokeRect(x, y, width, height);

    if (location !== "" && y > 6 * scale) {
      const [r = 0, g = 0, b = 0] = ctx.getImageData(Math.floor(x + width / 2), Math.floor(y / 2), 1, 1).data;
      const nameColor = 0.2126 * r + 0.7152 * g + 0.0722 * b > 128
        ? "--sounding-name-on-light" : "--sounding-name-on-dark";
      ctx.fillStyle = theme.getPropertyValue(nameColor).trim();
      ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
      ctx.font = `${String(Math.min(42 * scale, y * 0.67))}px Arial, sans-serif`;
      ctx.fillText(location, x + width / 2, y * 0.8, width - 2 * inset);
    }
    const image = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((blob) => { if (blob === null) reject(new Error("The sounding locator could not be saved.")); else resolve(blob); }, "image/png");
    });
    return { ...view, image };
  } finally {
    sounding.close(); map?.close();
  }
}

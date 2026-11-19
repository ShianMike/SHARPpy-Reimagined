import type { SoundingMapSnapshot } from "../../map/runtime/soundingLocator";
import type { SoundingView } from "../../types/soundingView";

/** Frame the captured map and put the place name on the sounding header row. */
export async function composeSoundingLocator(view: SoundingView, snapshot: SoundingMapSnapshot,
  location: string): Promise<SoundingView> {
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
    const [r = 0, g = 0, b = 0] = ctx.getImageData(Math.floor(x + width / 2), Math.floor(y / 2), 1, 1).data;
    const contrastingColor = theme.getPropertyValue(0.2126 * r + 0.7152 * g + 0.0722 * b > 128
      ? "--sounding-name-on-light" : "--sounding-name-on-dark").trim();
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(map, x, y, width, height);

    // Keep the outline around the captured pixels and visible at fit-to-window scale.
    const thickness = Math.max(1, 4 * scale), half = thickness / 2;
    const left = Math.max(half, x - half), top = Math.max(half, y - half);
    const right = Math.min(view.width - half, x + width + half);
    const bottom = Math.min(view.height - half, y + height + half);
    ctx.strokeStyle = contrastingColor; ctx.lineWidth = thickness;
    ctx.strokeRect(left, top, right - left, bottom - top);

    // Marker is computed from MapLibre's actual projection, including map pitch.
    const px = x + snapshot.point.x * width, py = y + snapshot.point.y * height;
    ctx.strokeStyle = halo; ctx.lineWidth = 5 * scale;
    ctx.beginPath(); ctx.arc(px, py, 6 * scale, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = foreground; ctx.lineWidth = 2 * scale;
    ctx.beginPath(); ctx.arc(px, py, 6 * scale, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = selected; ctx.fill();

    if (location !== "" && y > 6 * scale) {
      ctx.fillStyle = contrastingColor;
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

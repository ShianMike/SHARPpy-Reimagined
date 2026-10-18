import { profileSettings } from "../lib/graphics/profileSettings";
import { useGraphicsStore } from "../stores/graphicsStore";
import { decodeRadarValues, type RadarValues } from "./radarValues";

interface DecodedPng { promise: Promise<ImageBitmap>; image?: ImageBitmap; values?: Promise<RadarValues | null>; samples?: RadarValues | null }
const images = new Map<Blob, DecodedPng>();

function trim(): void {
  const budget = profileSettings(useGraphicsStore.getState().effective.profile).cpuFieldCacheMb * 1024 * 1024 / 2;
  const size = (item: DecodedPng): number => (item.image === undefined ? 0 : item.image.width * item.image.height * 4) + (item.samples?.rows.byteLength ?? 0);
  let bytes = [...images.values()].reduce((total, item) => total + size(item), 0);
  for (const [key, item] of images) {
    if (bytes <= budget) break;
    if (item.image === undefined) continue;
    bytes -= size(item);
    images.delete(key);
    // shortcut: GC releases evicted bitmaps still drawn by panels; add reference counting if eager close is needed.
  }
}

/** Share PNG decoding across panels and prepare playback before changing the clock. */
export function decodePng(png: Blob): Promise<ImageBitmap> {
  const cached = images.get(png);
  if (cached !== undefined) {
    images.delete(png); images.set(png, cached);
    return cached.promise;
  }
  const entry: DecodedPng = { promise: createImageBitmap(png).then((image) => {
    entry.image = image;
    trim();
    return image;
  }, (error: unknown) => { images.delete(png); throw error; }) };
  images.set(png, entry);
  return entry.promise;
}

export function cachedPng(png: Blob): ImageBitmap | undefined { return images.get(png)?.image; }

/** Only displayed native radar frames need their indices; playback preloading still decodes just the image. */
export async function radarValues(png: Blob): Promise<RadarValues | null> {
  await decodePng(png);
  const entry = images.get(png);
  if (entry === undefined) return null;
  return entry.values ??= decodeRadarValues(png).then((samples) => {
    entry.samples = samples;
    trim();
    return samples;
  });
}

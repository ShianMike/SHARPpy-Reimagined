import { expectArray, expectExactKeys, expectFiniteNumber, expectInteger, expectNullable, expectObject } from "../lib/contracts/guards";
import type { Bounds } from "../types/soundings";

export interface RadarValues {
  width: number;
  height: number;
  /** Unfiltered indexed PNG rows, each still preceded by its filter byte. */
  rows: Uint8Array;
  values: readonly (number | null)[];
  folded: number | null;
  lowerBounds: ReadonlySet<number>;
}

function paeth(left: number, up: number, corner: number): number {
  const prediction = left + up - corner;
  const a = Math.abs(prediction - left), b = Math.abs(prediction - up), c = Math.abs(prediction - corner);
  return a <= b && a <= c ? left : b <= c ? up : corner;
}

/** Read the native codes, never reverse a colour ramp or interpolate a measurement. */
export async function decodeRadarValues(png: Blob): Promise<RadarValues | null> {
  if (png.size > 32 * 1024 * 1024) throw new Error("Radar PNG is too large");
  const bytes = new Uint8Array(await png.arrayBuffer());
  if (bytes.length < 33 || [137, 80, 78, 71, 13, 10, 26, 10].some((value, i) => bytes[i] !== value)) return null;
  const view = new DataView(bytes.buffer);
  if (view.getUint32(8) !== 13 || view.getUint32(12) !== 0x49484452) return null;
  const width = view.getUint32(16), height = view.getUint32(20);
  // shortcut: our native radar renderer writes non-interlaced, 8-bit indexed PNGs; add other formats only when a numeric provider needs them.
  if (width < 1 || height < 1 || width > 4096 || height > 4096 || bytes[24] !== 8 || bytes[25] !== 3 || bytes[26] !== 0 || bytes[27] !== 0 || bytes[28] !== 0) return null;
  let metadata: unknown;
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  const decoder = new TextDecoder();
  let ended = false;
  for (let offset = 8; offset + 12 <= bytes.length;) {
    const length = view.getUint32(offset);
    if (length > bytes.length - offset - 12) throw new Error("Truncated radar PNG");
    const type = decoder.decode(bytes.subarray(offset + 4, offset + 8));
    const data = bytes.subarray(offset + 8, offset + 8 + length);
    if (type === "IDAT") chunks.push(data);
    if (type === "tEXt" && decoder.decode(data.subarray(0, 14)) === "sharppy-radar\0") {
      if (length > 16_384) throw new Error("Radar calibration is too large");
      metadata = JSON.parse(decoder.decode(data.subarray(14)));
    }
    offset += length + 12;
    if (type === "IEND") { ended = true; break; }
  }
  if (metadata === undefined) return null;
  if (!ended || chunks.length === 0) throw new Error("Incomplete radar PNG");
  const object = expectObject(metadata, "radar");
  expectExactKeys(object, "radar", ["values", "folded", "lowerBounds"]);
  if (!Array.isArray(object.values) || object.values.length !== 256) throw new Error("Expected 256 radar gate values");
  const values = expectArray(object.values, "radar.values", (value, path) => expectNullable(value, path, expectFiniteNumber));
  const code = (value: unknown, path: string): number => expectInteger(value, path, { min: 0, max: 255 });
  const folded = expectNullable(object.folded, "radar.folded", (value, path) => expectInteger(value, path, { min: 1, max: 1 }));
  if (values[0] !== null || values[1] !== null) throw new Error("Missing radar gates must not have values");
  if (!Array.isArray(object.lowerBounds) || object.lowerBounds.length > 256) throw new Error("Invalid radar height flags");
  const lowerBounds = new Set(expectArray(object.lowerBounds, "radar.lowerBounds", code));
  const stride = width + 1;
  const rows = new Uint8Array(stride * height);
  const reader = new Blob(chunks).stream().pipeThrough(new DecompressionStream("deflate")).getReader();
  let filled = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (filled + value.length > rows.length) throw new Error("Radar pixels exceed their dimensions");
      rows.set(value, filled);
      filled += value.length;
    }
  } finally {
    await reader.cancel();
  }
  if (filled !== rows.length) throw new Error("Truncated radar pixels");
  for (let row = 0; row < height; row += 1) {
    const first = row * stride + 1;
    const filter = rows[first - 1] ?? 0;
    if (filter > 4) throw new Error("Unsupported radar PNG filter");
    if (filter === 0) continue;
    for (let col = 0; col < width; col += 1) {
      const index = first + col;
      const left = col > 0 ? rows[index - 1] ?? 0 : 0;
      const up = row > 0 ? rows[index - stride] ?? 0 : 0;
      const corner = row > 0 && col > 0 ? rows[index - stride - 1] ?? 0 : 0;
      rows[index] = (rows[index] ?? 0) + (filter === 1 ? left : filter === 2 ? up : filter === 3 ? Math.floor((left + up) / 2) : paeth(left, up, corner));
    }
  }
  return { width, height, rows, values, folded, lowerBounds };
}

export function radarCodeAt(samples: RadarValues, bounds: Bounds, longitude: number, latitude: number): number {
  const { west, east, south, north } = bounds;
  if (!(longitude >= west && longitude <= east && latitude >= south && latitude <= north)) return 0;
  const column = Math.min(samples.width - 1, Math.floor((longitude - west) / (east - west) * samples.width));
  const row = Math.min(samples.height - 1, Math.floor((north - latitude) / (north - south) * samples.height));
  return samples.rows[row * (samples.width + 1) + column + 1] ?? 0;
}

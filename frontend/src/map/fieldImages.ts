import type { TextureSource } from "@deck.gl/core";
import type { FieldFrame, Palette } from "../types/map";
import { parseColour } from "./canvasTokens";
import { buildLut, cellColours, LUT_SIZE, paletteRange } from "./field/colourize";
import type { FieldImage } from "./layers/registry";

interface ColouredField { palette: Palette; image: FieldImage; pixels?: ImageData }
const images = new WeakMap<FieldFrame, ColouredField>();
const grids = new WeakMap<FieldFrame, ImageData>();
const palettes = new WeakMap<Palette, ImageData>();

export interface FieldSamplingProps {
  fieldPalette: TextureSource;
  fieldTransform: [number, number, number, number];
  fieldRange: [number, number];
}

// shortcut: check a 4 ms task budget every 65536 cells; retune the chunk if hardware checks show an overrun.
const SLICE_CELLS = 65536;

/** The same byte packing for lazy display and cancellable playback preparation. */
function* fieldPixels(frame: FieldFrame, palette: Palette | null): Generator<void, ImageData> {
  const bytes = new Uint8ClampedArray(frame.cells.length * 4);
  const table = palette === null ? null : cellColours(frame, palette);
  const pixels = new Uint32Array(bytes.buffer);
  for (let start = 0; start < frame.cells.length; start += SLICE_CELLS) {
    const end = Math.min(start + SLICE_CELLS, frame.cells.length);
    if (table !== null) {
      for (let i = start; i < end; i++) pixels[i] = table[frame.cells[i] ?? frame.nodata] ?? 0;
    } else {
      for (let i = start; i < end; i++) {
        const cell = frame.cells[i] ?? frame.nodata;
        // Opaque RG preserves all 16 bits; B is validity, independent of the palette floor.
        bytes[i * 4] = cell >> 8;
        bytes[i * 4 + 1] = cell & 255;
        bytes[i * 4 + 2] = cell === frame.nodata ? 0 : 255;
        bytes[i * 4 + 3] = 255;
      }
    }
    if (end < frame.cells.length) yield;
  }
  return new ImageData(bytes, frame.width, frame.height);
}

function pack(frame: FieldFrame, palette: Palette | null): ImageData {
  const chunks = fieldPixels(frame, palette);
  let chunk = chunks.next();
  while (!chunk.done) chunk = chunks.next();
  return chunk.value;
}

function fieldEntry(frame: FieldFrame, palette: Palette): ColouredField {
  const cached = images.get(frame);
  if (cached?.palette === palette) return cached;
  const entry: ColouredField = { palette, image: {
    get image() { return entry.pixels ??= pack(frame, palette); },
    bounds: frame.bounds, frame, palette,
  } };
  images.set(frame, entry);
  return entry;
}

/** Share lazy Sharp pixels with preparation; the query's frame owns their lifetime. */
export function fieldImage(frame: FieldFrame, palette: Palette): FieldImage { return fieldEntry(frame, palette).image; }

function paletteImage(palette: Palette): ImageData {
  let image = palettes.get(palette);
  if (image !== undefined) return image;
  if (palette.stepped) {
    const size = palette.stops.length;
    const bytes = new Uint8ClampedArray(size * 12);
    const edge = new DataView(new ArrayBuffer(4));
    palette.stops.forEach(([value, colour], i) => {
      bytes.set(parseColour(colour, "palette colour"), i * 4);
      edge.setFloat32(0, value, true);
      const bits = edge.getUint32(0, true);
      // Opaque RGB rows preserve every float bit through browser image uploads.
      bytes.set([bits & 255, (bits >>> 8) & 255, (bits >>> 16) & 255, 255], (size + i) * 4);
      bytes.set([bits >>> 24, 0, 0, 255], (size * 2 + i) * 4);
    });
    image = new ImageData(bytes, size, 3);
  } else {
    image = new ImageData(new Uint8ClampedArray(buildLut(palette)), LUT_SIZE, 1);
  }
  palettes.set(palette, image);
  return image;
}

export function smoothFieldProps({ frame, palette }: FieldImage): FieldSamplingProps & { image: ImageData } {
  let image = grids.get(frame);
  if (image === undefined) { image = pack(frame, null); grids.set(frame, image); }
  const { low, high } = paletteRange(palette);
  return {
    image, fieldPalette: paletteImage(palette),
    fieldTransform: [frame.scale, frame.offset, palette.floor ?? low, palette.ceiling ?? 3.4e38],
    fieldRange: [low, high > low ? high - low : 1],
  };
}

/** Prepare just this frame and sampling mode; publish no partial image after cancellation. */
export async function prepareFieldImage(frame: FieldFrame, palette: Palette, smoothing: boolean, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  const smooth = smoothing && palette.units !== "flag";
  const entry = fieldEntry(frame, palette);
  const chunks = fieldPixels(frame, smooth ? null : palette);
  while ((smooth ? grids.get(frame) : entry.pixels) === undefined) {
    if (typeof scheduler !== "undefined" && typeof scheduler.yield === "function") await scheduler.yield();
    else await new Promise<void>((resolve) => { setTimeout(resolve, 0); });
    signal.throwIfAborted();
    // A manual selection may have finished the same lazy image during the yield.
    if ((smooth ? grids.get(frame) : entry.pixels) !== undefined) break;
    const start = performance.now();
    let chunk = chunks.next();
    while (!chunk.done && performance.now() - start < 4) chunk = chunks.next();
    if (chunk.done) {
      if (smooth) grids.set(frame, chunk.value);
      else entry.pixels = chunk.value;
    }
  }
  if (smooth) paletteImage(palette);
}

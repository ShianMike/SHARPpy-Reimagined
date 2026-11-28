// Field colouring (REQUEST §7): the backend sends numbers, the frontend picks
// the pixels. Stepped palettes use their exact class edges; continuous palettes
// use a 1024-entry RGB-interpolated table. A cell with no data, below the floor (the first
// stop when there is none), or above the ceiling is transparent; a value past
// the last stop takes the last colour.
//
// Sharp maps cells through a CPU table; Smooth applies the same classes on the
// GPU after reconstructing the field values between samples.

import type { FieldFrame, Palette } from "../../types/map";

export const LUT_SIZE = 1024;
const CELL_VALUES = 65536;

const PTYPE_STOPS_PER_GROUP = 17;
const PTYPE_GROUP_COUNT = 4;
// Sharp's default type names; a palette's own ptypeLabels replace them.
const PTYPE_GROUP_LABELS = ["Rain", "Snow", "Freezing rain", "Mix"] as const;

/**
 * The encoded group offsets of a ptype ramp: four identical 17-stop bands
 * spaced 10 units apart for precipitation rates (0.01–0.5) or 100 units apart
 * for reflectivity (dBZ, such as 5–50 dBZ).
 */
function ptypeGroupOffsets(palette: Palette): readonly [number, number, number, number] | null {
  if (!palette.stepped) return null;
  const stops = palette.stops;
  if (stops.length !== PTYPE_GROUP_COUNT * PTYPE_STOPS_PER_GROUP) return null;
  const first0 = stops[0]?.[0];
  const first1 = stops[PTYPE_STOPS_PER_GROUP]?.[0];
  const top0 = stops[PTYPE_STOPS_PER_GROUP - 1]?.[0];
  if (first0 === undefined || first1 === undefined || top0 === undefined) return null;
  const stride = Number((first1 - first0).toFixed(2));
  if ((stride !== 10 && stride !== 100) || !(first0 > 0) || !(top0 < stride)) return null;
  for (let group = 1; group < PTYPE_GROUP_COUNT; group += 1) {
    for (let band = 0; band < PTYPE_STOPS_PER_GROUP; band += 1) {
      const base = stops[band]?.[0];
      const shifted = stops[group * PTYPE_STOPS_PER_GROUP + band]?.[0];
      if (base === undefined || shifted === undefined || Number((shifted - base).toFixed(2)) !== group * stride) {
        return null;
      }
    }
  }
  return [0, stride, stride * 2, stride * 3];
}

/**
 * The stride between a ptype ramp's groups: the precip rates pack their four
 * groups 10 units apart, while HRRR reflectivity packs dBZ per group 100 apart.
 * Null when the palette is not a ptype ramp.
 */
export function ptypeGroupStride(palette: Palette): 10 | 100 | null {
  const offsets = ptypeGroupOffsets(palette);
  if (offsets === null) return null;
  return offsets[1] === 100 ? 100 : 10;
}

export function isPtypePalette(palette: Palette): boolean {
  return ptypeGroupStride(palette) !== null;
}

export function ptypePaletteGroups(palette: Palette): { label: string; stops: Palette["stops"] }[] | null {
  const offsets = ptypeGroupOffsets(palette);
  if (offsets === null || ptypeGroupStride(palette) === null) return null;
  return offsets.map((offset, index) => {
    const start = index * PTYPE_STOPS_PER_GROUP;
    return {
      label: palette.ptypeLabels?.[index] ?? PTYPE_GROUP_LABELS[index] ?? "",
      stops: palette.stops.slice(start, start + PTYPE_STOPS_PER_GROUP)
        .map(([value, colour]) => [Number((value - offset).toFixed(2)), colour] as const),
    };
  });
}

/** Tick locations are the lower edges of equal-width colour bands. */
export function ptypePaletteTicks(ticks: Palette["ticks"], stops: Palette["stops"]): { value: number; at: number }[] {
  return stops.flatMap(([value], index) => index === 0 || index === stops.length - 1 || ticks.includes(value)
    ? [{ value, at: index / stops.length }]
    : []);
}

export function decodePtypeValue(value: number, palette: Palette): { amount: number; ptypeLabel: string | null } {
  const stride = ptypeGroupStride(palette);
  const offsets = ptypeGroupOffsets(palette);
  if (stride === null || offsets === null) return { amount: value, ptypeLabel: null };
  const index = Math.min(PTYPE_GROUP_COUNT - 1, Math.max(0, Math.floor(value / stride)));
  const offset = offsets[index] ?? 0;
  return {
    amount: Math.max(0, Number((value - offset).toFixed(2))),
    ptypeLabel: palette.ptypeLabels?.[index] ?? PTYPE_GROUP_LABELS[index] ?? null,
  };
}

function hexChannels(hex: string): [number, number, number] {
  return [Number.parseInt(hex.slice(1, 3), 16), Number.parseInt(hex.slice(3, 5), 16), Number.parseInt(hex.slice(5, 7), 16)];
}

export interface PaletteRange {
  low: number;
  high: number;
}

/** The palette's drawn axis: its first and last stop. */
export function paletteRange(palette: Palette): PaletteRange {
  return { low: palette.stops[0]?.[0] ?? 0, high: palette.stops[palette.stops.length - 1]?.[0] ?? 1 };
}

/** RGBA, LUT_SIZE entries over the palette's axis (np.linspace(low, high, 1024)). */
export function buildLut(palette: Palette): Uint8Array {
  const { low, high } = paletteRange(palette);
  const positions = palette.stops.map(([value]) => value);
  const colours = palette.stops.map(([, colour]) => hexChannels(colour));
  const lut = new Uint8Array(LUT_SIZE * 4);
  for (let i = 0; i < LUT_SIZE; i += 1) {
    const value = low + ((high - low) * i) / (LUT_SIZE - 1);
    // The highest stop at or below the value (searchsorted side="right", minus one).
    let slot = 0;
    while (slot + 1 < positions.length && (positions[slot + 1] ?? Infinity) <= value) {
      slot += 1;
    }
    let colour = colours[slot] ?? [0, 0, 0];
    const next = colours[slot + 1];
    const from = positions[slot] ?? value;
    const to = positions[slot + 1];
    if (!palette.stepped && next !== undefined && to !== undefined && to > from) {
      const t = (value - from) / (to - from);
      // np.interp, then astype(uint8): truncation.
      colour = [0, 1, 2].map((c) => Math.trunc((colour[c] ?? 0) + t * ((next[c] ?? 0) - (colour[c] ?? 0)))) as [number, number, number];
    }
    lut.set([colour[0], colour[1], colour[2], 255], i * 4);
  }
  return lut;
}

/** One packed RGBA colour per possible cell value of a frame; 0 is transparent. */
export function cellColours(frame: Pick<FieldFrame, "scale" | "offset" | "nodata">, palette: Palette): Uint32Array {
  // Matching Uint32 views preserve RGBA byte order on either endianness.
  const lut = palette.stepped ? null : new Uint32Array(buildLut(palette).buffer);
  const colours = new Uint32Array(Uint8Array.from(palette.stops.flatMap(([, colour]) => [...hexChannels(colour), 255])).buffer);
  const { low, high } = paletteRange(palette);
  const span = high > low ? high - low : 1;
  const floor = palette.floor ?? low;
  const ceiling = palette.ceiling ?? Number.POSITIVE_INFINITY;
  const table = new Uint32Array(CELL_VALUES);
  let band = 0;
  for (let cell = 0; cell < CELL_VALUES; cell += 1) {
    const value = cell * frame.scale + frame.offset;
    if (cell === frame.nodata || value < floor || value > ceiling) {
      continue;
    }
    if (lut === null) {
      while (band + 1 < palette.stops.length && (palette.stops[band + 1]?.[0] ?? Infinity) <= value) band += 1;
      table[cell] = colours[band] ?? 0;
    } else {
      const slot = Math.trunc(Math.min(1, Math.max(0, (value - low) / span)) * (LUT_SIZE - 1));
      table[cell] = lut[slot] ?? 0;
    }
  }
  return table;
}

/** The frame as RGBA pixels, row 0 at the north edge (ImageData order). */
export function colourize(frame: FieldFrame, palette: Palette): Uint8ClampedArray<ArrayBuffer> {
  const table = cellColours(frame, palette);
  const out = new Uint8ClampedArray(frame.cells.length * 4);
  const pixels = new Uint32Array(out.buffer);
  const { cells } = frame;
  for (let i = 0; i < cells.length; i += 1) {
    pixels[i] = table[cells[i] ?? frame.nodata] ?? 0;
  }
  return out;
}

/** The value of the cell under a point, or null outside the frame or where it has no data. */
export function sampleAt(frame: FieldFrame, longitude: number, latitude: number): number | null {
  const { west, east, south, north } = frame.bounds;
  if (!(longitude >= west && longitude <= east && latitude >= south && latitude <= north)) {
    return null;
  }
  const column = Math.min(frame.width - 1, Math.floor(((longitude - west) / (east - west)) * frame.width));
  const row = Math.min(frame.height - 1, Math.floor(((north - latitude) / (north - south)) * frame.height));
  const cell = frame.cells[row * frame.width + column];
  return cell === undefined || cell === frame.nodata ? null : cell * frame.scale + frame.offset;
}

/** A note when a reading exceeds the palette's ceiling. */
export function drawnRangeNote(value: number, palette: Palette): string {
  return palette.ceiling !== null && value > palette.ceiling ? "above the drawn range" : "";
}

/**
 * A readout number: whole units from 100, one decimal from 1, two below. A
 * value within quantization of zero reads "0.00", never "-0.00".
 */
export function formatValue(value: number): string {
  const magnitude = Math.abs(value);
  if (!Number.isFinite(value)) return "\u2014";
  const text = value.toFixed(magnitude >= 100 ? 0 : magnitude >= 1 ? 1 : 2);
  return /^-0(?:\.0+)?$/u.test(text) ? text.slice(1) : text;
}

/** Keep neighbouring scale labels distinct, including small values and narrow ranges. */
export function formatScaleTick(value: number, span: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  const range = Math.abs(span);
  const digits = range > 0 && Number.isFinite(range)
    ? Math.max(3, Math.min(17, 3 + Math.floor(Math.log10(Math.abs(value))) - Math.floor(Math.log10(range)))) : 3;
  return String(Number(value.toPrecision(digits)));
}

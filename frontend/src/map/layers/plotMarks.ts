// The station model as drawn marks (REQUEST surface observations): pure
// geometry over the record's display slots and mark spec. Screen pixels, y
// down; each piece states the screen angle of its own axis in degrees from
// east, clockwise — the same frame `angleDeg` arrives in.

import type { SurfacePlot } from "../../types/surface";

/**
 * One glyph piece of a plot. `axisDeg` is the screen angle the glyph's
 * forward axis should point along (degrees from east, y down); the layer
 * turns it into deck.gl's counter-clockwise rotation in one place.
 */
export interface PlotMark {
  text: string;
  dx: number;
  dy: number;
  axisDeg: number;
  /** The glyph's own forward axis at zero rotation (90 for a vertical bar). */
  baseDeg: number;
  size: number;
  tone: "ink" | "dim" | "warn" | "upper" | "lower";
  /** Shapes sit centred on their offset; the plot's labels start at it. */
  kind: "mark" | "text" | "wind";
}

const RING = "\u25cb";
const DASHED_RING = "\u25cc";
const CROSS = "\u00d7";
const SQUARE = "\u25a1";
const QUESTION = "?";

function piece(text: string, axisDeg: number, baseDeg: number, size: number, tone: PlotMark["tone"]): PlotMark {
  return { text, dx: 0, dy: 0, axisDeg, baseDeg, size, tone, kind: "mark" };
}

/** A plot's own label: the reading pair and the identifier. */
function label(text: string, dx: number, dy: number, size: number, tone: PlotMark["tone"]): PlotMark {
  return { text, dx, dy, axisDeg: 0, baseDeg: 0, size, tone, kind: "text" };
}

/** One mark as deck.gl's TextLayer wants it: rotation and a screen offset. */
export interface MarkPlacement {
  /** Counter-clockwise degrees, deck.gl's own convention. */
  angle: number;
  /** Screen pixels; deck.gl adds this offset after rotating the glyph. */
  offset: readonly [number, number];
}

/**
 * The marks are laid out in the paint path's frame (x right, y down, angles
 * clockwise from east). deck.gl rotates the glyph counter-clockwise, then
 * adds its pixel offset in screen space.
 */
export function markPlacement(mark: PlotMark): MarkPlacement {
  const angle = mark.baseDeg - mark.axisDeg;
  return {
    angle,
    offset: [mark.dx, mark.dy],
  };
}

/**
 * One plot's full mark: the wind mark or its state glyph, the status ring of
 * a checked station, the stale ring, the reading pair, and the identifier.
 * Shapes carry the state distinctions (T22), never hue alone.
 */
export function plotMarks(plot: SurfacePlot): PlotMark[] {
  const marks: PlotMark[] = [];
  if (plot.availability !== "available") {
    // Checked without a match rings solid and crosses; a failed query rings
    // dashed and asks. Both name themselves at the ring's shoulder.
    marks.push(piece(plot.availability === "unknown" ? DASHED_RING : RING, 0, 0, 12, plot.availability === "unknown" ? "warn" : "dim"));
    marks.push(piece(plot.availability === "unknown" ? QUESTION : CROSS, 0, 0, 7, plot.availability === "unknown" ? "warn" : "dim"));
    marks.push(label(plot.id, 8, 4, 13, "dim"));
    return marks;
  }
  if (plot.stale) {
    marks.push(piece(DASHED_RING, 0, 0, 12, "dim"));
  }
  if (plot.staff !== null) {
    const { angleDeg, pennants, barbs, halves } = plot.staff;
    marks.push({ ...piece(String(pennants * 50 + barbs * 10 + halves * 5), angleDeg, 0, 24, "ink"), kind: "wind" });
  } else if (plot.windState === "calm") {
    marks.push({ ...piece("0", 0, 0, 24, "ink"), kind: "wind" });
  } else if (plot.windState === "missing") {
    marks.push(piece(CROSS, 0, 0, 8, "ink"));
  } else {
    marks.push(piece(SQUARE, 0, 0, 8, "ink"));
    marks.push({ ...piece(QUESTION, 0, 0, 8, "ink"), dx: 5, dy: -5 });
  }
  marks.push(label(plot.upperText, -28, -4, 15, "upper"));
  marks.push(label(plot.lowerText, -28, 17, 15, "lower"));
  marks.push(label(plot.id, 7, 17, 13, "dim"));
  return marks;
}

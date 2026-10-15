import { describe, expect, it } from "vitest";
import type { SurfacePlot } from "../../types/surface";
import { markPlacement, plotMarks, type PlotMark } from "./plotMarks";

const plot = (over: Partial<SurfacePlot>): SurfacePlot => ({
  id: "KOUN",
  longitude: -97.4,
  latitude: 35.2,
  valid: "2026-10-04T21:00:00Z",
  offsetMin: 0,
  stale: false,
  availability: "available",
  windState: "available",
  staff: { angleDeg: 180, pennants: 0, barbs: 2, halves: 0 },
  upperText: "22",
  lowerText: "12",
  label: "KOUN",
  description: "",
  ...over,
});

function find(marks: PlotMark[], text: string): PlotMark {
  const mark = marks.find((item) => item.text === text);
  expect(mark, `a ${text} mark`).toBeDefined();
  return mark as PlotMark;
}

describe("plot marks", () => {
  it("uses one complete speed icon with the provider's source direction", () => {
    const marks = plotMarks(plot({ staff: { angleDeg: 225, pennants: 1, barbs: 3, halves: 1 } }));
    const wind = find(marks, "85");
    expect(wind.kind).toBe("wind");
    expect([wind.dx, wind.dy]).toEqual([0, 0]);
    expect(markPlacement(wind).angle).toBe(-225);
    expect(marks.filter((mark) => mark.kind === "wind")).toHaveLength(1);
  });

  it("gives every plot state its own shape", () => {
    const calm = plotMarks({ ...plot({ windState: "calm", staff: null }) });
    expect(find(calm, "0").kind).toBe("wind");
    const missing = plotMarks({ ...plot({ windState: "missing", staff: null }) });
    expect(find(missing, "\u00d7").size).toBe(8);
    const partial = plotMarks({ ...plot({ windState: "direction-missing", staff: null }) });
    expect(find(partial, "\u25a1")).toBeDefined();
    expect(find(partial, "?")).toBeDefined();
    const none = plotMarks({ ...plot({ availability: "unavailable", windState: "missing", staff: null, valid: null, offsetMin: null }) });
    expect(find(none, "\u25cb").tone).toBe("dim");
    expect(find(none, "\u00d7")).toBeDefined();
    const failed = plotMarks({ ...plot({ availability: "unknown", windState: "missing", staff: null, valid: null, offsetMin: null }) });
    expect(find(failed, "\u25cc").tone).toBe("warn");
    expect(find(failed, "?")).toBeDefined();
  });

  it("keeps one warning colour throughout a stale plot", () => {
    const marks = plotMarks({ ...plot({ stale: true }) });
    expect(find(marks, "\u25cc").size).toBe(54);
    for (const mark of marks) {
      expect(mark.tone).toBe("warn");
    }
  });

  it("places the reading pair and the identifier like the station model stands", () => {
    const marks = plotMarks(plot({}));
    const upper = find(marks, "22");
    expect([upper.dx, upper.dy]).toEqual([-28, -4]);
    const lower = find(marks, "12");
    expect([lower.dx, lower.dy]).toEqual([-28, 17]);
    const id = find(marks, "KOUN");
    expect([id.dx, id.dy]).toEqual([7, 17]);
  });

  it("turns the paint path's frame into deck.gl's own", () => {
    // A vertical glyph standing in for a west staff turns a quarter turn; the
    // offset stays in screen space: deck adds it after rotating the glyph.
    const west = markPlacement({
      text: "|", dx: -12, dy: 0, axisDeg: 180, baseDeg: 90, size: 24, tone: "ink", kind: "mark",
    });
    expect(west.angle).toBe(-90);
    expect(west.offset).toEqual([-12, 0]);
    // An unrotated mark keeps its screen offset untouched.
    const flat = markPlacement({
      text: "22", dx: -23, dy: -4, axisDeg: 0, baseDeg: 0, size: 11, tone: "upper", kind: "text",
    });
    expect(flat.angle).toBe(0);
    expect([...flat.offset]).toEqual([-23, -4]);
  });
});

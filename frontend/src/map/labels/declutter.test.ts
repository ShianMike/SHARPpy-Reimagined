import { describe, expect, it } from "vitest";
import type { Station } from "../../types/contracts";
import { declutter, type DeclutterOptions } from "./declutter";

function station(id: string, x: number, y: number): Station {
  // The position is encoded in the coordinates so the test projection is trivial.
  return { id, name: "", latitude: y, longitude: x, source: "FM35", availability: "unknown" };
}

const base: Omit<DeclutterOptions, "selectedId"> = {
  project: (s) => ({ x: s.longitude, y: s.latitude }),
  viewport: { width: 200, height: 200 },
  cell: 64,
};

const ids = (list: Station[]): string[] => list.map((s) => s.id);

describe("declutter", () => {
  it("keeps one label per screen cell, the lowest id first", () => {
    const stations = [station("b", 10, 10), station("a", 20, 20), station("c", 100, 10)];
    expect(ids(declutter(stations, { ...base, selectedId: null }))).toEqual(["a", "c"]);
  });

  it("always keeps the selected station in its cell", () => {
    const stations = [station("a", 20, 20), station("z", 30, 30), station("c", 100, 10)];
    expect(ids(declutter(stations, { ...base, selectedId: "z" }))).toEqual(["z", "c"]);
  });

  it("is deterministic whatever the input order", () => {
    const stations = [station("d", 150, 150), station("b", 10, 10), station("a", 20, 20), station("c", 100, 10)];
    const first = ids(declutter(stations, { ...base, selectedId: null }));
    const second = ids(declutter([...stations].reverse(), { ...base, selectedId: null }));
    expect(first).toEqual(["a", "c", "d"]);
    expect(second).toEqual(first);
  });

  it("drops stations outside the viewport or with no finite projection", () => {
    const stations = [station("a", -5, 10), station("b", 10, 250), station("c", Number.NaN, 10), station("d", 5, 5)];
    expect(ids(declutter(stations, { ...base, selectedId: "a" }))).toEqual(["d"]);
  });

  it("keeps one of two stations a few pixels apart across a cell edge, on either axis", () => {
    expect(ids(declutter([station("a", 62, 10), station("b", 66, 12)], { ...base, selectedId: null }))).toEqual(["a"]);
    expect(ids(declutter([station("a", 10, 62), station("b", 12, 66)], { ...base, selectedId: null }))).toEqual(["a"]);
    // A cell apart on one axis is clear, however close on the other.
    expect(ids(declutter([station("a", 10, 10), station("b", 74, 12)], { ...base, selectedId: null }))).toEqual(["a", "b"]);
  });

  it("uses the cell size it is given", () => {
    const stations = [station("a", 10, 10), station("b", 60, 10)];
    expect(ids(declutter(stations, { ...base, cell: 48, selectedId: null }))).toEqual(["a", "b"]);
    expect(ids(declutter(stations, { ...base, cell: 96, selectedId: null }))).toEqual(["a"]);
  });

  it("does not modify its input", () => {
    const stations = [station("b", 10, 10), station("a", 100, 100)];
    declutter(stations, { ...base, selectedId: null });
    expect(ids(stations)).toEqual(["b", "a"]);
  });
});

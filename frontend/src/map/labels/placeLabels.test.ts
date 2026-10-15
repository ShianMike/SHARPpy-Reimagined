import { describe, expect, it } from "vitest";
import type { Place } from "../../types/map";
import { selectPlaceLabels } from "./placeLabels";

const place = (name: string, longitude: number, latitude: number, tier: 1 | 2): Place => ({ name, longitude, latitude, tier });
const places = [
  place("Dallas, Texas", -96.8, 32.8, 1),
  place("Fort Worth, Texas", -97.3, 32.75, 1),
  place("Norman, Oklahoma", -97.44, 35.22, 2),
  place("Far Away, Maine", -69, 45, 2),
];
// One degree is 10 px, from the west edge at -100 and the north edge at 40.
const input = {
  places,
  bounds: { west: -100, east: -90, south: 30, north: 40 },
  project: (p: Place) => ({ x: (p.longitude + 100) * 10, y: (40 - p.latitude) * 10 }),
  viewport: { width: 100, height: 100 },
  cell: 20,
  reserved: [],
  reservedCell: 10,
};

describe("place labels", () => {
  it("shows major cities at every span and towns from 30 degrees in", () => {
    expect(selectPlaceLabels({ ...input, span: 60 }).map((p) => p.name)).toEqual(["Dallas, Texas"]);
    expect(selectPlaceLabels({ ...input, span: 20 }).map((p) => p.name)).toEqual(["Dallas, Texas", "Norman, Oklahoma"]);
  });

  it("keeps the first name per cell, in order, and keeps clear of station labels", () => {
    // Fort Worth (27, 72.5) is 5 px from Dallas (32, 72), inside its 20 px cell.
    // Norman (25.6, 47.8) is on a station label (10 px cells), then a cell left
    // of one (its name runs right over it), then just past one: drawn.
    expect(selectPlaceLabels({ ...input, span: 20, reserved: [{ x: 26, y: 48 }] }).map((p) => p.name)).toEqual(["Dallas, Texas"]);
    expect(selectPlaceLabels({ ...input, span: 20, reserved: [{ x: 35, y: 48 }] }).map((p) => p.name)).toEqual(["Dallas, Texas"]);
    expect(selectPlaceLabels({ ...input, span: 20, reserved: [{ x: 15, y: 48 }] }).map((p) => p.name)).toEqual([
      "Dallas, Texas",
      "Norman, Oklahoma",
    ]);
  });

  it("keeps clear of a station label just across a cell edge", () => {
    // 3 px below Norman, in the next 10 px row: still under the name.
    expect(selectPlaceLabels({ ...input, span: 20, reserved: [{ x: 24, y: 51 }] }).map((p) => p.name)).toEqual(["Dallas, Texas"]);
    // Half a cell below or more is clear.
    expect(selectPlaceLabels({ ...input, span: 20, reserved: [{ x: 24, y: 53 }] }).map((p) => p.name)).toEqual([
      "Dallas, Texas",
      "Norman, Oklahoma",
    ]);
  });
});

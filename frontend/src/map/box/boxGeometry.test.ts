import { describe, expect, it } from "vitest";
import { boxBetween, boxCentre, boxContains, boxDimensions, boxRing, moveBox, resizeBox } from "./boxGeometry";

describe("geographic box gestures", () => {
  it("makes the same region in either drag direction and rejects empty gestures", () => {
    const expected = { west: -98, east: -97, south: 35, north: 36 };
    expect(boxBetween([-98, 35], [-97, 36])).toEqual(expected);
    expect(boxBetween([-97, 36], [-98, 35])).toEqual(expected);
    expect(boxBetween([-98, 35], [-98, 36])).toBeNull();
    expect(boxBetween([-98, 35], [-97, 35])).toBeNull();
  });
  it("wraps longitude, clips latitude, and retains the geographic region when moved", () => {
    const box = boxBetween([179, 35], [-179, 36]);
    expect(box).toEqual({ west: 179, east: -179, south: 35, north: 36 });
    if (box === null) throw new Error("expected box");
    expect(boxCentre(box)).toEqual([-180, 35.5]);
    expect(boxContains(box, [180, 35.5])).toBe(true);
    expect(boxContains(box, [0, 35.5])).toBe(false);
    expect(moveBox(box, [179, 35], [-179, 36])).toEqual({ west: -179, east: -177, south: 36, north: 37 });
    const dimensions = boxDimensions(box);
    expect(dimensions[0]).toBeGreaterThan(180);
    expect(dimensions[1]).toBeCloseTo(111.195, 2);
  });
  it("densifies parallel edges without changing the region across projections", () => {
    const box = { west: 179, east: -179, south: 35, north: 36 };
    const ring = boxRing(box);
    expect(ring).toHaveLength(129);
    expect(ring[0]).toEqual(ring.at(-1));
    expect(Math.max(...ring.map(([longitude]) => longitude ?? 0))).toBe(181);
    expect(new Set(ring.slice(0, 32).map(([, latitude]) => latitude))).toEqual(new Set([35]));
  });
  it("keeps resize handles on their own side when dragged past the opposite corner", () => {
    const box = resizeBox([-97, 36], 0, [-96, 37]);
    expect(box?.east).toBe(-97);
    expect(box?.north).toBe(36);
    expect(box?.west).toBeLessThan(-97);
    expect(box?.south).toBeLessThan(36);
    expect(resizeBox([-179, 36], 0, [179, 35])).toEqual({ west: 179, east: -179, south: 35, north: 36 });
  });
});

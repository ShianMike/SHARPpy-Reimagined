import { describe, expect, it } from "vitest";
import { nearestRadarSite } from "./nearestSite";

const SITES = [
  { id: "KTLX", latitude: 35.33, longitude: -97.28 },
  { id: "KDDC", latitude: 37.76, longitude: -99.97 },
  { id: "KINX", latitude: 36.18, longitude: -95.56 },
  // High-latitude site: raw longitude difference would misrank it.
  { id: "PAPD", latitude: 65.04, longitude: -147.5 },
];

describe("nearestRadarSite", () => {
  it("picks the closest antenna to the point", () => {
    // Norman, OK: KTLX is nearest, KINX second.
    expect(nearestRadarSite(SITES, 35.2, -97.4)).toBe("KTLX");
    // Dodge City: KDDC wins despite KTLX being further east.
    expect(nearestRadarSite(SITES, 37.75, -100.0)).toBe("KDDC");
  });

  it("prefers the great-circle distance, not the raw degree difference", () => {
    // Near the Alaskan site: one degree of longitude is ~57 km at 65 N, so the
    // raw coordinate comparison against KINX would pick the wrong antenna.
    expect(nearestRadarSite(SITES, 65.0, -147.4)).toBe("PAPD");
  });

  it("returns null for an empty catalogue", () => {
    expect(nearestRadarSite([], 35.0, -97.0)).toBeNull();
  });
});

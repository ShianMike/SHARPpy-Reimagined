import { describe, expect, it } from "vitest";
import type { Station, StationCatalog } from "../types/contracts";
import type { AvailabilityReport } from "../types/map";
import { recentReleases, withAvailability } from "./availability";

const station = (id: string): Station => ({ id, name: id, latitude: 35, longitude: -97, source: "FM35", availability: "unknown" });
const catalog: StationCatalog = {
  stations: [station("72357"), station("72451"), station("74560")],
  origin: "bundled",
  availabilityBasis: { kind: "catalogOnly" },
  droppedCount: 0,
};
const report: AvailabilityReport = {
  observationTime: "2026-10-04T12:00:00Z",
  listedIds: ["72357", "74560"],
  extraStations: [{ ...station("72400"), availability: "listed" }, { ...station("72451"), availability: "listed" }],
  droppedCount: 2,
};

describe("station availability", () => {
  it("marks the catalogue against the UWyo list and adds the stations it lacks", () => {
    const merged = withAvailability(catalog, report);
    expect(merged.stations.map((s) => [s.id, s.availability])).toEqual([
      ["72357", "listed"],
      ["72400", "listed"],
      ["72451", "notListed"],
      ["74560", "listed"],
    ]);
    expect(merged.availabilityBasis).toEqual({ kind: "listedAt", observationTime: "2026-10-04T12:00:00Z" });
    expect(merged.droppedCount).toBe(2);
    // One array per catalogue and report, so deck.gl and the indexes keep theirs.
    expect(withAvailability(catalog, report)).toBe(merged);
    expect(catalog.stations[0]?.availability).toBe("unknown");
  });

  it("offers the newest settled 00Z and 12Z releases, newest first", () => {
    expect(recentReleases(new Date("2026-10-04T13:30:00Z"), 3)).toEqual([
      "2026-10-04T00:00:00Z",
      "2026-10-03T12:00:00Z",
      "2026-10-03T00:00:00Z",
    ]);
    expect(recentReleases(new Date("2026-10-04T14:00:00Z"), 1)).toEqual(["2026-10-04T12:00:00Z"]);
    expect(recentReleases(new Date("2026-10-04T01:00:00Z"), 1)).toEqual(["2026-10-03T12:00:00Z"]);
  });
});

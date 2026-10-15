import catalogFull from "@contracts/public/stations.catalog.full.json";
import { describe, expect, it } from "vitest";
import { parseStationCatalog } from "../lib/contracts/stations";
import { resolveSelection, stationIndex } from "./stationIndex";
import {
  UNNAMED_STATION,
  availabilityExplanation,
  availabilityText,
  displayName,
  formatCoordinates,
  formatLatitude,
  formatLongitude,
  sourceText,
  stationTitle,
} from "./stationFormat";

const stations = parseStationCatalog(catalogFull).stations;
const byId = stationIndex(stations);
function station(id: string) {
  const found = byId.get(id);
  if (found === undefined) {
    throw new Error(`station ${id} is not in the golden catalogue`);
  }
  return found;
}

describe("displayName", () => {
  it("normalizes the multi-line 73111 name to 123 characters", () => {
    const name = displayName(station("73111").name);
    expect(name).toBe(
      "Stationid 73111 Kapuskasing 73111 Kapuskasing Name: Name, Dtype: Str, Stationid 73111 Ca 73111 Ca Name: Country, Dtype: Str",
    );
    expect(name).toHaveLength(123);
  });

  it("collapses runs of spaces (72206)", () => {
    expect(displayName(station("72206").name)).toBe("Jacksonville/Intnl., Fl., Usa");
  });

  it("names empty and whitespace-only names Unnamed station", () => {
    expect(displayName("")).toBe(UNNAMED_STATION);
    expect(displayName(" \n\t ")).toBe(UNNAMED_STATION);
    expect(displayName(station("03023").name)).toBe(UNNAMED_STATION);
  });
});

describe("coordinates and text", () => {
  it("formats hemispheres with two decimals", () => {
    expect(formatLatitude(35.18095)).toBe("35.18° N");
    expect(formatLongitude(-97.43787)).toBe("97.44° W");
    expect(formatLatitude(-49.35131)).toBe("49.35° S");
    expect(formatLongitude(70.24138)).toBe("70.24° E");
    expect(formatLatitude(-0.001)).toBe("0.00° N");
    expect(formatCoordinates(station("72357"))).toBe("35.18° N, 97.44° W");
  });

  it("formats source, availability, and the one-line title", () => {
    expect(sourceText("FM35")).toBe("UWyo FM35");
    expect(availabilityText("unknown")).toBe("Unknown");
    expect(availabilityExplanation("unknown")).toBe("The bundled catalogue does not record availability.");
    expect(stationTitle(station("72357"))).toBe("72357 · Norman/Max Westheimer A, Ok., Usa");
  });
});

describe("selection lookup", () => {
  it("resolves an id against the catalogue", () => {
    expect(resolveSelection(null, stations)).toEqual({ state: "none" });
    expect(resolveSelection("72357", undefined)).toEqual({ state: "waiting", id: "72357" });
    expect(resolveSelection("72357", stations)).toMatchObject({ state: "found", station: { id: "72357" } });
    expect(resolveSelection("XXXXX", stations)).toEqual({ state: "missing", id: "XXXXX" });
    expect(stationIndex(stations)).toBe(byId);
    expect(byId.size).toBe(933);
  });
});

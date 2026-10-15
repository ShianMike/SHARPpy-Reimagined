import catalogFull from "@contracts/public/stations.catalog.full.json";
import searchCases from "@contracts/public/stations.search-cases.json";
import { describe, expect, it } from "vitest";
import { parseStationCatalog } from "../lib/contracts/stations";
import { FOLD, MAX_QUERY_LENGTH, SEARCH_LIMIT, fold, pythonStrip, searchStations } from "./stationSearch";

const stations = parseStationCatalog(catalogFull).stations;
const ids = (query: string, limit?: number): string[] => searchStations(stations, query, limit).map((s) => s.id);

describe("searchStations parity with search_stations()", () => {
  it("covers the generated cases", () => {
    expect(searchCases.cases.length).toBeGreaterThanOrEqual(13);
  });

  it.each(searchCases.cases.map((c) => [JSON.stringify(c.query), c] as const))(
    "query %s returns the ids Python returns",
    (_label, testCase) => {
      expect(testCase.limit).toBe(SEARCH_LIMIT);
      expect(ids(testCase.query, testCase.limit)).toEqual(testCase.ids);
    },
  );
});

describe("searchStations edge cases", () => {
  it("returns nothing for blank and whitespace-only queries", () => {
    expect(ids("")).toEqual([]);
    expect(ids(" \t\n\u00a0\u3000")).toEqual([]);
  });

  it("returns an exact, case-sensitive id match alone", () => {
    expect(ids("72357")).toEqual(["72357"]);
    // "723" is an id prefix, not an exact id: substring matches, sorted, limited.
    expect(ids("723")).toHaveLength(SEARCH_LIMIT);
    const sorted = [...ids("723")].sort();
    expect(ids("723")).toEqual(sorted);
  });

  it("cuts the query at 64 characters before trimming", () => {
    const padded = `72357${" ".repeat(MAX_QUERY_LENGTH)}`;
    expect(ids(padded)).toEqual(["72357"]);
    const longTail = `${" ".repeat(MAX_QUERY_LENGTH - 1)}72357`;
    // The cut keeps only "7", which matches by substring.
    expect(ids(longTail)).toEqual(ids("7"));
    expect(ids(`${"x".repeat(MAX_QUERY_LENGTH)}72357`)).toEqual([]);
  });

  it("matches names case-insensitively and honours the limit", () => {
    expect(ids("norman")).toEqual(ids("NORMAN"));
    expect(ids("a", 3)).toHaveLength(3);
  });

  it("trims with Python's whitespace set, not trim()", () => {
    expect(pythonStrip("\u001c72357\u001f")).toBe("72357");
    expect(pythonStrip("\u008572357")).toBe("72357");
    expect(pythonStrip("\ufeff72357")).toBe("\ufeff72357");
    expect(pythonStrip("\u2000\u200a72357\u205f")).toBe("72357");
  });

  it("folds every FOLD entry to ASCII before lower-casing", () => {
    expect(Object.keys(FOLD)).toHaveLength(11);
    for (const [char, folded] of Object.entries(FOLD)) {
      expect(fold(char)).toBe(folded);
      expect(/^[a-z]+$/.test(fold(char))).toBe(true);
    }
    expect(fold("STRASSE")).toBe("strasse");
    expect(fold("Stra\u00DFe")).toBe("strasse");
    expect(fold("\u212Aelvin")).toBe("kelvin");
  });
});

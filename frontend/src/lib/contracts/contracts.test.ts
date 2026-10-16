import { describe, expect, it } from "vitest";
import { BACKEND_ERROR_CODES } from "../../types/contracts";
import { parseBackendError } from "./errors";
import { ContractError } from "./guards";
import { parseBackendHealth, parseBridgeStatus } from "./health";
import { parseStationCatalog } from "./stations";

const publicFixtures = import.meta.glob<unknown>("@contracts/public/*.json", { eager: true, import: "default" });
const errorFixtures = import.meta.glob<unknown>("@contracts/public/errors/*.json", { eager: true, import: "default" });
const invalidFixtures = import.meta.glob<unknown>("@contracts/public/invalid/*.json", { eager: true, import: "default" });

function baseName(path: string): string {
  return (path.split("/").pop() ?? path).replace(/\.json$/, "");
}

function fixture(name: string): unknown {
  const entry = Object.entries(publicFixtures).find(([path]) => baseName(path) === name);
  if (entry === undefined) {
    throw new Error(`missing public fixture ${name}`);
  }
  return entry[1];
}

/** The parser a fixture's <target> names (C7): stations.catalog, health, or error. */
function parserFor(name: string): (value: unknown) => unknown {
  if (name.startsWith("stations.catalog.")) {
    return parseStationCatalog;
  }
  if (name.startsWith("health.")) {
    return parseBackendHealth;
  }
  if (name.startsWith("error.")) {
    return parseBackendError;
  }
  throw new Error(`no parser for fixture ${name}`);
}

describe("public fixtures", () => {
  const dtoNames = Object.keys(publicFixtures)
    .map(baseName)
    .filter((name) => name !== "stations.search-cases");

  it("finds every positive DTO fixture", () => {
    expect(dtoNames.sort()).toEqual([
      "health.backend-unavailable",
      "health.ok",
      "health.python-fallback",
      "health.python-unavailable",
      "health.restarting",
      "health.unknown",
      "stations.catalog.full",
      "stations.catalog.sample",
      "stations.catalog.seed-fallback",
    ]);
  });

  it.each(dtoNames)("%s parses into an equal new object", (name) => {
    const raw = fixture(name);
    const parsed = parserFor(name)(raw);
    expect(parsed).toEqual(raw);
    expect(parsed).not.toBe(raw);
  });

  it("parses both positive health shapes", () => {
    const rust = parseBackendHealth(fixture("health.ok"));
    const python = parseBackendHealth(fixture("health.python-fallback"));
    expect(rust.scientificBackend).toMatchObject({ status: "ok", activeBackend: "rust", rustVersion: "3.0.2" });
    expect(python.scientificBackend).toMatchObject({ status: "ok", activeBackend: "python", rustVersion: null });
    expect(parseBackendHealth(fixture("health.restarting")).bridge.status).toBe("restarting");
    expect(parseBackendHealth(fixture("health.unknown")).scientificBackend?.status).toBe("unknown");
  });

  it("parses the golden catalogue and the seed fallback", () => {
    const full = parseStationCatalog(fixture("stations.catalog.full"));
    expect(full.stations).toHaveLength(933);
    expect(full.origin).toBe("bundled");
    expect(full.availabilityBasis).toEqual({ kind: "catalogOnly" });
    expect(full.stations.every((station) => station.availability === "unknown")).toBe(true);
    const seed = parseStationCatalog(fixture("stations.catalog.seed-fallback"));
    expect(seed.stations).toHaveLength(10);
    expect(seed.origin).toBe("seedFallback");
  });

  it("parses the bridge-status payload shapes used by events", () => {
    const bridge = (fixture("health.ok") as { bridge: unknown }).bridge;
    expect(parseBridgeStatus(bridge, "")).toEqual(bridge);
    expect(parseBridgeStatus({ status: "starting", since: "2026-10-04T01:02:03Z" })).toEqual({
      status: "starting",
      since: "2026-10-04T01:02:03Z",
    });
  });
});

describe("error fixtures", () => {
  it("BACKEND_ERROR_CODES equals the file set of public/errors/", () => {
    expect(Object.keys(errorFixtures).map(baseName).sort()).toEqual([...BACKEND_ERROR_CODES].sort());
  });

  it.each(Object.entries(errorFixtures).map(([path, value]) => [baseName(path), value] as const))(
    "%s parses with parseBackendError",
    (code, value) => {
      const parsed = parseBackendError(value);
      expect(parsed).not.toBeNull();
      expect(parsed?.code).toBe(code);
      expect(parsed).toEqual(value);
    },
  );

  it("returns null for values that are not error-shaped", () => {
    for (const value of [undefined, null, "command not allowed", 42, [], new Error("boom"), { message: "x" }, { code: 7 }]) {
      expect(parseBackendError(value)).toBeNull();
    }
  });
});

describe("invalid fixtures", () => {
  const cases = Object.entries(invalidFixtures).map(([path, value]) => [baseName(path), value] as const);

  it("covers every invalid public fixture", () => {
    expect(cases.map(([name]) => name).sort()).toEqual([
      "error.missing-retryable",
      "error.unknown-code",
      "health.unknown-bridge-status",
      "stations.catalog.available-true",
      "stations.catalog.empty-id",
      "stations.catalog.latitude-91",
      "stations.catalog.unknown-origin",
    ]);
  });

  it.each(cases)("%s is rejected with a ContractError by the parser its target names", (name, value) => {
    const parse = parserFor(name);
    let caught: unknown;
    try {
      parse(value);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ContractError);
    expect((caught as ContractError).path).not.toBe("");
  });

  it("names the failing path", () => {
    const expectPath = (run: () => unknown, path: string, reason?: string): void => {
      try {
        run();
        expect.unreachable();
      } catch (error) {
        expect(error).toBeInstanceOf(ContractError);
        expect((error as ContractError).path).toBe(path);
        if (reason !== undefined) {
          expect((error as ContractError).message).toBe(`${path}: ${reason}`);
        }
      }
    };
    const invalid = (name: string): unknown =>
      Object.entries(invalidFixtures).find(([path]) => baseName(path) === name)?.[1];
    expectPath(
      () => parseStationCatalog(invalid("stations.catalog.latitude-91")),
      "stations[0].latitude",
      "expected a finite number in [-90, 90], got 91",
    );
    expectPath(() => parseStationCatalog(invalid("stations.catalog.available-true")), "stations[0].source", "is required");
    expectPath(() => parseStationCatalog(invalid("stations.catalog.empty-id")), "stations[0].id");
    expectPath(() => parseStationCatalog(invalid("stations.catalog.unknown-origin")), "origin");
    expectPath(() => parseBackendHealth(invalid("health.unknown-bridge-status")), "bridge.status");
    expectPath(() => parseBackendError(invalid("error.unknown-code")), "error.code");
    expectPath(() => parseBackendError(invalid("error.missing-retryable")), "error.retryable", "is required");
  });

  it("rejects duplicate ids, unknown keys, and bad timestamps", () => {
    const full = fixture("stations.catalog.full") as { stations: unknown[] };
    const duplicated = { ...full, stations: [...full.stations, full.stations[0]] };
    expect(() => parseStationCatalog(duplicated)).toThrow(/stations\[933\]\.id: duplicate id "01001"/);
    const health = fixture("health.ok") as Record<string, unknown>;
    expect(() => parseBackendHealth({ ...health, extra: 1 })).toThrow(/extra: is not part of the contract/);
    expect(() => parseBackendHealth({ ...health, checkedAt: "2026-10-04T01:02:05.123Z" })).toThrow(ContractError);
    expect(() => parseBackendHealth({ ...health, checkedAt: "2026-10-04 01:02:05" })).toThrow(ContractError);
  });
});

import type { BoxSoundingMetadata } from "../../types/boxSounding";
import { at, ContractError, expectArray, expectBoolean, expectExactKeys, expectFiniteNumber, expectInteger, expectObject, expectString } from "./guards";
import { parseBounds } from "./soundings";

export function parseBoxSounding(value: unknown, path: string): BoxSoundingMetadata {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["bounds", "widthKm", "heightKm", "areaKm2", "gridResolutionKm", "gridType", "selectedColumns", "validColumns",
    "surfacePressureHpa", "method", "location", "sourceCacheHit", "profileCacheHit", "coverage", "timings"]);
  const bounds = parseBounds(obj.bounds, at(path, "bounds"));
  const width = bounds.east > bounds.west ? bounds.east - bounds.west : 360 + bounds.east - bounds.west;
  if (bounds.south >= bounds.north || width <= 0 || width > 180) throw new ContractError(path, "expected a nonempty geographic box of at most 180 degrees longitude");
  const number = (key: string): number => expectFiniteNumber(obj[key], at(path, key), { min: Number.MIN_VALUE });
  const text = (key: string): string => expectString(obj[key], at(path, key), { minLength: 1, maxLength: 120 });
  const selectedColumns = expectInteger(obj.selectedColumns, at(path, "selectedColumns"), { min: 1, max: 65536 });
  const validColumns = expectInteger(obj.validColumns, at(path, "validColumns"), { min: 1, max: selectedColumns });
  const coverage = expectArray(obj.coverage, at(path, "coverage"), (value, p) => {
    const row = expectObject(value, p);
    expectExactKeys(row, p, ["pressureHpa", "validColumns", "fraction", "retained"]);
    return { pressureHpa: expectFiniteNumber(row.pressureHpa, at(p, "pressureHpa"), { min: Number.MIN_VALUE }),
      validColumns: expectInteger(row.validColumns, at(p, "validColumns"), { min: 0, max: validColumns }),
      fraction: expectFiniteNumber(row.fraction, at(p, "fraction"), { min: 0, max: 1 }), retained: expectBoolean(row.retained, at(p, "retained")) };
  });
  if (coverage.length === 0 || coverage.length > 10000) throw new ContractError(path, "invalid coverage length");
  const rawTimings = expectObject(obj.timings, at(path, "timings"));
  if (Object.keys(rawTimings).length > 20) throw new ContractError(path, "too many timing stages");
  const timings = Object.fromEntries(Object.entries(rawTimings).map(([key, value]) => [key,
    expectFiniteNumber(value, at(path, `timings.${key}`), { min: 0 })]));
  return { bounds, widthKm: number("widthKm"), heightKm: number("heightKm"), areaKm2: number("areaKm2"), gridResolutionKm: number("gridResolutionKm"),
    gridType: text("gridType"), selectedColumns, validColumns, surfacePressureHpa: number("surfacePressureHpa"), method: text("method"),
    location: text("location"), sourceCacheHit: expectBoolean(obj.sourceCacheHit, at(path, "sourceCacheHit")),
    profileCacheHit: expectBoolean(obj.profileCacheHit, at(path, "profileCacheHit")), coverage, timings };
}

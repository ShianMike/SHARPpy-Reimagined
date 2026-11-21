// Parsers for the surface station plots (DESIGN-contracts C8): strict keys,
// bounded text, finite numbers, and nothing aliased. Every rule mirrors the
// Rust validator in `dto/surface.rs`, which runs first.

import type { StaffSpec, SurfacePlot, SurfacePlots } from "../../types/surface";
import {
  ContractError,
  at,
  expectArray,
  expectBoolean,
  expectExactKeys,
  expectFiniteNumber,
  expectInteger,
  expectNullable,
  expectObject,
  expectOneOf,
  expectString,
  expectTimestamp,
} from "./guards";

function parseStaff(value: unknown, path: string): StaffSpec {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["angleDeg", "pennants", "barbs", "halves"]);
  const angleDeg = expectFiniteNumber(obj.angleDeg, at(path, "angleDeg"), { min: 0 });
  // The bridge wraps the direction into [0, 360); 360 itself is not a member,
  // the same half-open interval the Rust validator allows.
  if (angleDeg >= 360) {
    throw new ContractError(at(path, "angleDeg"), "must be within [0, 360)");
  }
  return {
    angleDeg,
    pennants: expectInteger(obj.pennants, at(path, "pennants"), { min: 0, max: 5 }),
    barbs: expectInteger(obj.barbs, at(path, "barbs"), { min: 0, max: 4 }),
    halves: expectInteger(obj.halves, at(path, "halves"), { min: 0, max: 1 }),
  };
}

function parsePlot(value: unknown, path: string, offsetLimit: number): SurfacePlot {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "id", "longitude", "latitude", "valid", "offsetMin", "stale",
    "availability", "windState", "staff", "upperText", "lowerText",
    "label", "description", "temperatureC", "dewpointC",
  ]);
  const valid = expectNullable(obj.valid, at(path, "valid"), expectTimestamp);
  const offsetMin = expectNullable(obj.offsetMin, at(path, "offsetMin"), (v, p) =>
    expectFiniteNumber(v, p, { min: -offsetLimit, max: offsetLimit }),
  );
  if ((valid === null) !== (offsetMin === null)) {
    throw new ContractError(at(path, "offsetMin"), "must belong to a plot with a time");
  }
  const windState = expectOneOf(obj.windState, at(path, "windState"), [
    "available", "calm", "missing", "direction-missing",
  ]);
  const staff = expectNullable(obj.staff, at(path, "staff"), parseStaff);
  if ((staff === null) === (windState === "available")) {
    throw new ContractError(at(path, "staff"), "belongs to an available wind only");
  }
  return {
    id: expectString(obj.id, at(path, "id"), { minLength: 1, maxLength: 16 }),
    longitude: expectFiniteNumber(obj.longitude, at(path, "longitude"), { min: -180, max: 180 }),
    latitude: expectFiniteNumber(obj.latitude, at(path, "latitude"), { min: -90, max: 90 }),
    valid,
    offsetMin,
    stale: expectBoolean(obj.stale, at(path, "stale")),
    availability: expectOneOf(obj.availability, at(path, "availability"), [
      "available", "unavailable", "unknown",
    ]),
    windState,
    staff,
    temperatureC: expectNullable(obj.temperatureC, at(path, "temperatureC"), expectFiniteNumber),
    dewpointC: expectNullable(obj.dewpointC, at(path, "dewpointC"), expectFiniteNumber),
    upperText: expectString(obj.upperText, at(path, "upperText"), { minLength: 1, maxLength: 4 }),
    lowerText: expectString(obj.lowerText, at(path, "lowerText"), { minLength: 1, maxLength: 4 }),
    label: expectString(obj.label, at(path, "label"), { minLength: 1, maxLength: 120 }),
    description: expectString(obj.description, at(path, "description"), { minLength: 1, maxLength: 600 }),
  };
}

function parsePlots(value: unknown, path: string, plotLimit: number, offsetLimit: number): SurfacePlots {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["validTime", "counts", "plots"]);
  const counts = expectObject(obj.counts, at(path, "counts"));
  expectExactKeys(counts, at(path, "counts"), [
    "matched", "stale", "noObservation", "failed", "nearby", "queried", "unqueried",
  ]);
  const count = (key: string): number => expectInteger(counts[key], at(path, `counts.${key}`), { min: 0 });
  const matched = count("matched");
  const stale = count("stale");
  const noObservation = count("noObservation");
  const failed = count("failed");
  const nearby = count("nearby");
  const queried = count("queried");
  const unqueried = count("unqueried");
  if (unqueried + queried !== nearby) {
    throw new ContractError(at(path, "counts"), "do not add up to the nearby stations");
  }
  const plotPath = at(path, "plots");
  if (Array.isArray(obj.plots) && obj.plots.length > plotLimit) {
    throw new ContractError(plotPath, "has too many station records");
  }
  const plots = expectArray(obj.plots, plotPath, (item, itemPath) => parsePlot(item, itemPath, offsetLimit));
  const ids = new Set<string>();
  const tally = { matched: 0, stale: 0, noObservation: 0, failed: 0 };
  for (const plot of plots) {
    if (ids.has(plot.id)) throw new ContractError(plotPath, "names one station twice");
    ids.add(plot.id);
    if (plot.stale) tally.stale++;
    if (plot.availability === "available") tally.matched++;
    else if (plot.availability === "unavailable") tally.noObservation++;
    else tally.failed++;
  }
  if (
    tally.matched !== matched ||
    tally.stale !== stale ||
    tally.noObservation !== noObservation ||
    tally.failed !== failed
  ) {
    throw new ContractError(at(path, "counts"), "disagree with the records");
  }
  return {
    validTime: expectTimestamp(obj.validTime, at(path, "validTime")),
    counts: { matched, stale, noObservation, failed, nearby, queried, unqueried },
    plots,
  };
}

export function parseSurfacePlots(value: unknown, path = ""): SurfacePlots {
  return parsePlots(value, path, 64, 60);
}

export function parseLatestSurfacePlots(value: unknown, path = ""): SurfacePlots {
  return parsePlots(value, path, 20_000, 24 * 60);
}

// Parsers for storm reports and the provider's control options
// (DESIGN-contracts C8): strict keys, bounded text, finite numbers, and
// nothing aliased.

import type { ReportOptions, ReportPoint, StormReports } from "../../types/map";
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
  expectString,
  expectTimestamp,
} from "./guards";
import { parseColour } from "./colour";

function parseReport(value: unknown, path: string): ReportPoint {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "label", "description", "fill", "stroke", "radiusPx", "symbol", "caption",
    "significant", "category", "valid", "longitude", "latitude",
  ]);
  return {
    label: expectString(obj.label, at(path, "label"), { minLength: 1, maxLength: 120 }),
    description: expectString(obj.description, at(path, "description"), { maxLength: 2000 }),
    fill: expectNullable(obj.fill, at(path, "fill"), parseColour),
    stroke: parseColour(obj.stroke, at(path, "stroke")),
    radiusPx: expectFiniteNumber(obj.radiusPx, at(path, "radiusPx"), { min: 0, max: 20 }),
    symbol: expectString(obj.symbol, at(path, "symbol"), { minLength: 1, maxLength: 16 }),
    caption: expectString(obj.caption, at(path, "caption"), { maxLength: 40 }),
    significant: expectBoolean(obj.significant, at(path, "significant")),
    category: expectString(obj.category, at(path, "category"), { maxLength: 24 }),
    valid: expectNullable(obj.valid, at(path, "valid"), expectTimestamp),
    longitude: expectFiniteNumber(obj.longitude, at(path, "longitude"), { min: -180, max: 180 }),
    latitude: expectFiniteNumber(obj.latitude, at(path, "latitude"), { min: -90, max: 90 }),
  };
}

export function parseStormReports(value: unknown, path = ""): StormReports {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "validTime", "windowS", "windowLabel", "hazardFilter", "totalCount", "shownCount",
    "subtitle", "validFrom", "validTo", "sourceUrl", "attribution", "reports",
  ]);
  const optionalTime = (v: unknown, p: string): string | null => expectNullable(v, p, expectTimestamp);
  const reports = expectArray(obj.reports, at(path, "reports"), parseReport);
  const shownCount = expectInteger(obj.shownCount, at(path, "shownCount"), { min: 0 });
  if (shownCount !== reports.length) {
    throw new ContractError(at(path, "shownCount"), "does not match the report records");
  }
  return {
    validTime: expectTimestamp(obj.validTime, at(path, "validTime")),
    windowS: expectInteger(obj.windowS, at(path, "windowS"), { min: 1 }),
    windowLabel: expectString(obj.windowLabel, at(path, "windowLabel"), { minLength: 1, maxLength: 24 }),
    hazardFilter: expectString(obj.hazardFilter, at(path, "hazardFilter"), { minLength: 1, maxLength: 24 }),
    totalCount: expectInteger(obj.totalCount, at(path, "totalCount"), { min: 0 }),
    shownCount,
    subtitle: expectString(obj.subtitle, at(path, "subtitle"), { maxLength: 300 }),
    validFrom: optionalTime(obj.validFrom, at(path, "validFrom")),
    validTo: optionalTime(obj.validTo, at(path, "validTo")),
    sourceUrl: expectString(obj.sourceUrl, at(path, "sourceUrl"), { maxLength: 2000 }),
    attribution: expectString(obj.attribution, at(path, "attribution"), { maxLength: 300 }),
    reports,
  };
}

export function parseReportOptions(value: unknown, path = ""): ReportOptions {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["windows", "filters"]);
  const windows = expectArray(obj.windows, at(path, "windows"), (item, p) => {
    const window = expectObject(item, p);
    expectExactKeys(window, p, ["label", "seconds"]);
    return {
      label: expectString(window.label, at(p, "label"), { minLength: 1, maxLength: 24 }),
      seconds: expectInteger(window.seconds, at(p, "seconds"), { min: 1 }),
    };
  });
  const filters = expectArray(obj.filters, at(path, "filters"), (item, p) => {
    const filter = expectObject(item, p);
    expectExactKeys(filter, p, ["key", "label"]);
    return {
      key: expectString(filter.key, at(p, "key"), { minLength: 1, maxLength: 24 }),
      label: expectString(filter.label, at(p, "label"), { minLength: 1, maxLength: 60 }),
    };
  });
  // The same bounds the Rust validator applies: one window to choose from, and
  // never more options than the provider documents.
  if (windows.length === 0 || windows.length > 8 || filters.length > 8) {
    throw new ContractError(at(path, "windows"), "expected 1 to 8 windows and at most 8 filters");
  }
  return { windows, filters };
}

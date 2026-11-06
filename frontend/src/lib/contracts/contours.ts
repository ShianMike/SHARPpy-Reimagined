// The parser for contour-line geometry (DESIGN-contracts C8): strict keys,
// bounded counts, and every point on the globe.

import type { ContourLines, WindBarbGrid } from "../../types/map";
import { parseBounds } from "./soundings";
import {
  ContractError,
  at,
  expectArray,
  expectExactKeys,
  expectFiniteNumber,
  expectInteger,
  expectNullable,
  expectObject,
  expectString,
  expectTimestamp,
} from "./guards";

const MAX_SEGMENTS = 100_000;
const MAX_PATH_POINTS = 200_000;

function parseWindGrid(value: unknown, path: string): WindBarbGrid {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["width", "height", "bounds", "dataBase64"]);
  const width = expectInteger(obj.width, at(path, "width"), { min: 1, max: 4096 });
  const height = expectInteger(obj.height, at(path, "height"), { min: 1, max: 4096 });
  const count = width * height;
  if (count > 8_000_000) throw new ContractError(path, "wind grid is too large");
  const encoded = expectString(obj.dataBase64, at(path, "dataBase64"), { maxLength: Math.ceil(count * 2 / 3) * 4 });
  let bytes: string;
  try {
    bytes = atob(encoded);
  } catch {
    throw new ContractError(path, "wind grid is not base64");
  }
  if (bytes.length !== count * 2) throw new ContractError(path, "wind grid has the wrong byte count");
  const data = new Uint16Array(count);
  for (let i = 0; i < count; i += 1) {
    const cell = bytes.charCodeAt(i * 2) | (bytes.charCodeAt(i * 2 + 1) << 8);
    if (cell !== 65535 && (cell & 511) > 359) throw new ContractError(path, "wind direction is invalid");
    data[i] = cell;
  }
  const bounds = parseBounds(obj.bounds, at(path, "bounds"));
  if (bounds.east <= bounds.west || bounds.north <= bounds.south) throw new ContractError(path, "wind bounds are empty");
  return { width, height, bounds, data };
}

function parsePoint(value: unknown, path: string): readonly [number, number] {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new ContractError(path, "expected a [lon, lat] pair");
  }
  return [
    expectFiniteNumber(value[0], at(path, 0), { min: -180, max: 180 }),
    expectFiniteNumber(value[1], at(path, 1), { min: -90, max: 90 }),
  ];
}

function parseSegment(
  value: unknown,
  path: string,
): readonly [readonly [number, number], readonly [number, number]] {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new ContractError(path, "expected two endpoints");
  }
  return [parsePoint(value[0], at(path, 0)), parsePoint(value[1], at(path, 1))];
}

export function parseContourLines(value: unknown, path = ""): ContourLines {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["product", "runTime", "forecastHour", "unit", "interval", "segments", "wind"], ["labels", "paths"]);
  const segments = expectArray(obj.segments, at(path, "segments"), parseSegment);
  const interval = expectFiniteNumber(obj.interval, at(path, "interval"), { min: 0, max: 1000 });
  // The same rule as the Rust validator: an interval must be positive, and a
  // level set with no spacing has no lines to draw.
  if (interval <= 0) {
    throw new ContractError(at(path, "interval"), "expected a positive interval");
  }
  if (segments.length > MAX_SEGMENTS) {
    throw new ContractError(at(path, "segments"), "has too many segments");
  }
  let remainingPoints = MAX_PATH_POINTS;
  const paths = obj.paths === undefined ? undefined : expectArray(obj.paths, at(path, "paths"), (value, p) => {
    if (!Array.isArray(value) || value.length < 2) throw new ContractError(p, "expected a path with at least two points");
    remainingPoints -= value.length;
    if (remainingPoints < 0) throw new ContractError(p, "has too many contour path points");
    return value.map((point: unknown, index: number) => parsePoint(point, at(p, index)));
  });
  const labels = obj.labels === undefined ? undefined : expectArray(obj.labels, at(path, "labels"), (value, p) => {
    const label = expectObject(value, p);
    expectExactKeys(label, p, ["position", "value"]);
    const position = parsePoint(label.position, at(p, "position"));
    const level = expectFiniteNumber(label.value, at(p, "value"));
    const multiple = level / interval;
    if (!Number.isFinite(multiple) || Math.abs(multiple - Math.round(multiple)) > 1e-8) {
      throw new ContractError(at(p, "value"), "label is not a contour level");
    }
    return {
      position,
      value: level,
    };
  });
  if (labels !== undefined && labels.length > 256) throw new ContractError(at(path, "labels"), "has too many contour labels");
  return {
    product: expectString(obj.product, at(path, "product"), { minLength: 1, maxLength: 40 }),
    runTime: expectTimestamp(obj.runTime, at(path, "runTime")),
    forecastHour: expectInteger(obj.forecastHour, at(path, "forecastHour"), { min: 0, max: 384 }),
    unit: expectString(obj.unit, at(path, "unit"), { maxLength: 40 }),
    interval,
    segments,
    wind: expectNullable(obj.wind, at(path, "wind"), parseWindGrid),
    ...(paths === undefined ? {} : { paths }),
    ...(labels === undefined ? {} : { labels }),
  };
}

// The parser for contour-line geometry (DESIGN-contracts C8): strict keys,
// bounded counts, and every point on the globe.

import type { ContourPath, PressureCenter, ThicknessContours } from "../../types/contours";
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
  expectOneOf,
  expectString,
  expectTimestamp,
} from "./guards";

const MAX_SEGMENTS = 100_000;
const MAX_PATH_POINTS = 200_000;
const MAX_CONTOUR_LABELS = 256;
const MAX_PRESSURE_LABELS = 2_048;
/** Pressure centers are few by nature; a frame with more is not a level set. */
const MAX_CENTERS = 64;

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

/** A joined display path with at least two on-globe points, shared by every level set. */
function parsePath(value: unknown, path: string, budget: { remaining: number }): ContourPath {
  if (!Array.isArray(value) || value.length < 2) throw new ContractError(path, "expected a path with at least two points");
  budget.remaining -= value.length;
  if (budget.remaining < 0) throw new ContractError(path, "has too many contour path points");
  return value.map((point: unknown, index: number) => parsePoint(point, at(path, index)));
}

/** One labelled contour level of `interval`, at its on-globe anchor. */
function parseContourLevel(value: unknown, path: string, interval: number): { position: readonly [number, number]; value: number } {
  const label = expectObject(value, path);
  expectExactKeys(label, path, ["position", "value"]);
  const level = expectFiniteNumber(label.value, at(path, "value"));
  const multiple = level / interval;
  if (!Number.isFinite(multiple) || Math.abs(multiple - Math.round(multiple)) > 1e-8) {
    throw new ContractError(at(path, "value"), "label is not a contour level");
  }
  return {
    position: parsePoint(label.position, at(path, "position")),
    value: level,
  };
}

function parseCenter(value: unknown, path: string): PressureCenter {
  const center = expectObject(value, path);
  expectExactKeys(center, path, ["lon", "lat", "kind", "value"]);
  return {
    lon: expectFiniteNumber(center.lon, at(path, "lon"), { min: -180, max: 180 }),
    lat: expectFiniteNumber(center.lat, at(path, "lat"), { min: -90, max: 90 }),
    kind: expectOneOf(center.kind, at(path, "kind"), ["H", "L"] as const),
    // Whole millibars: a center pressure is reported, never a grid value.
    value: expectInteger(center.value, at(path, "value"), { min: 300, max: 1200 }),
  };
}

function parseThickness(value: unknown, path: string): ThicknessContours {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["unit", "interval", "paths", "labels"]);
  const interval = expectFiniteNumber(obj.interval, at(path, "interval"), { min: 0, max: 1000 });
  if (interval <= 0) {
    throw new ContractError(at(path, "interval"), "expected a positive interval");
  }
  const budget = { remaining: MAX_PATH_POINTS };
  const paths = expectArray(obj.paths, at(path, "paths"), (item, p) => {
    const contour = expectObject(item, p);
    expectExactKeys(contour, p, ["value", "points"]);
    return {
      value: expectFiniteNumber(contour.value, at(p, "value")),
      points: parsePath(contour.points, at(p, "points"), budget),
    };
  });
  const labels = expectArray(obj.labels, at(path, "labels"), (item, p) => parseContourLevel(item, p, interval));
  if (labels.length > MAX_CONTOUR_LABELS) throw new ContractError(at(path, "labels"), "has too many contour labels");
  return {
    unit: expectString(obj.unit, at(path, "unit"), { maxLength: 40 }),
    interval,
    paths,
    labels,
  };
}

export function parseContourLines(value: unknown, path = ""): ContourLines {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["product", "runTime", "forecastHour", "unit", "interval", "segments", "wind"], ["labels", "paths", "centers", "thickness"]);
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
  const budget = { remaining: MAX_PATH_POINTS };
  const paths = obj.paths === undefined ? undefined : expectArray(obj.paths, at(path, "paths"), (value, p) => parsePath(value, p, budget));
  const unit = expectString(obj.unit, at(path, "unit"), { maxLength: 40 });
  const labels = obj.labels === undefined
    ? undefined
    : expectArray(obj.labels, at(path, "labels"), (value, p) => parseContourLevel(value, p, interval));
  const labelLimit = unit === "hPa" ? MAX_PRESSURE_LABELS : MAX_CONTOUR_LABELS;
  if (labels !== undefined && labels.length > labelLimit) throw new ContractError(at(path, "labels"), "has too many contour labels");
  const centers = obj.centers === undefined ? undefined : expectArray(obj.centers, at(path, "centers"), parseCenter);
  if (centers !== undefined && centers.length > MAX_CENTERS) throw new ContractError(at(path, "centers"), "has too many pressure centers");
  const thickness = obj.thickness === undefined ? undefined : parseThickness(obj.thickness, at(path, "thickness"));
  return {
    product: expectString(obj.product, at(path, "product"), { minLength: 1, maxLength: 40 }),
    runTime: expectTimestamp(obj.runTime, at(path, "runTime")),
    forecastHour: expectInteger(obj.forecastHour, at(path, "forecastHour"), { min: 0, max: 384 }),
    unit,
    interval,
    segments,
    wind: expectNullable(obj.wind, at(path, "wind"), parseWindGrid),
    ...(paths === undefined ? {} : { paths }),
    ...(labels === undefined ? {} : { labels }),
    ...(centers === undefined ? {} : { centers }),
    ...(thickness === undefined ? {} : { thickness }),
  };
}

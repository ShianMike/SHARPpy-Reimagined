// SHRF binary frames (docs/DATA_CONTRACTS.md): get_weather_field and
// get_radar_frame answer with one, an ArrayBuffer from Tauri. Layout: "SHRF",
// u16 version 1, u16 0, u32 header length N (all little-endian), N bytes of
// JSON padded with spaces so the body starts on an 8-byte boundary, then the
// body: uint16 field cells, or the radar service's PNG.

import type { FieldFrame, RadarFrame, RadarSiteFrame, SatelliteFrame } from "../../types/map";
import {
  ContractError,
  at,
  expectExactKeys,
  expectFiniteNumber,
  expectInteger,
  expectNullable,
  expectObject,
  expectOneOf,
  expectString,
  expectTimestamp,
  type JsonObject,
} from "./guards";
import { parseBounds, parsePng } from "./soundings";
import { parsePalette } from "./map";

const MAGIC = [0x53, 0x48, 0x52, 0x46];
const FIXED = 12;
const MAX_CELLS = 8_000_000;
const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

export interface ShrfFrame {
  header: JsonObject;
  /** A view of the response; nobody else holds the buffer, so nothing is copied. */
  body: Uint8Array;
}

function bytesOf(value: unknown, path: string): Uint8Array {
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }
  if (value instanceof Uint8Array) {
    return value;
  }
  if (Array.isArray(value) && value.every((b) => typeof b === "number")) {
    return Uint8Array.from(value);
  }
  throw new ContractError(path, "expected an SHRF frame");
}

export function parseShrf(value: unknown, path = ""): ShrfFrame {
  const bytes = bytesOf(value, path);
  if (bytes.length < FIXED || MAGIC.some((b, i) => bytes[i] !== b)) {
    throw new ContractError(path, "expected an SHRF frame");
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint16(4, true) !== 1) {
    throw new ContractError(path, "expected SHRF version 1");
  }
  const length = view.getUint32(8, true);
  if (FIXED + length > bytes.length) {
    throw new ContractError(path, "the SHRF header runs past the frame");
  }
  let header: unknown;
  try {
    header = JSON.parse(new TextDecoder().decode(bytes.subarray(FIXED, FIXED + length)));
  } catch {
    throw new ContractError(at(path, "header"), "expected JSON");
  }
  return { header: expectObject(header, at(path, "header")), body: bytes.subarray(FIXED + length) };
}

/** Builds a frame; the fixture transport and the tests use it. */
export function encodeShrf(header: unknown, body: Uint8Array): ArrayBuffer {
  const encoder = new TextEncoder();
  let json = JSON.stringify(header);
  while ((FIXED + encoder.encode(json).length) % 8 !== 0) {
    json += " ";
  }
  const head = encoder.encode(json);
  const out = new Uint8Array(FIXED + head.length + body.length);
  out.set(MAGIC, 0);
  const view = new DataView(out.buffer);
  view.setUint16(4, 1, true);
  view.setUint16(6, 0, true);
  view.setUint32(8, head.length, true);
  out.set(head, FIXED);
  out.set(body, FIXED + head.length);
  return out.buffer;
}

const FIELD_KEYS = [
  "kind", "product", "runTime", "forecastHour", "validTime", "width", "height", "bounds", "units",
  "scale", "offset", "nodata", "valueMin", "valueMax", "sourceUrl", "attribution",
] as const;

function orderedBounds(value: unknown, path: string): FieldFrame["bounds"] {
  const bounds = parseBounds(value, path);
  if (bounds.west >= bounds.east || bounds.south >= bounds.north) {
    throw new ContractError(path, "expected west < east and south < north");
  }
  return bounds;
}

/** get_weather_field: the header and a Uint16Array of `width * height` cells. */
export function parseFieldFrame(value: unknown, path = ""): FieldFrame {
  const { header: h, body } = parseShrf(value, path);
  const hp = at(path, "header");
  expectExactKeys(h, hp, FIELD_KEYS);
  expectOneOf(h.kind, at(hp, "kind"), ["field"]);
  const width = expectInteger(h.width, at(hp, "width"), { min: 1 });
  const height = expectInteger(h.height, at(hp, "height"), { min: 1, max: Math.floor(MAX_CELLS / width) });
  if (body.length !== width * height * 2) {
    throw new ContractError(at(path, "body"), `expected ${String(width * height * 2)} bytes`);
  }
  // ponytail: a little-endian host is assumed (x86-64 and arm64, every target);
  // the copy happens only when the body is not 2-byte aligned.
  const aligned = body.byteOffset % 2 === 0 ? body : body.slice();
  const scale = expectFiniteNumber(h.scale, at(hp, "scale"));
  if (scale <= 0) {
    throw new ContractError(at(hp, "scale"), "expected a positive scale");
  }
  return {
    product: expectString(h.product, at(hp, "product"), { minLength: 1, maxLength: 40 }),
    runTime: expectTimestamp(h.runTime, at(hp, "runTime")),
    forecastHour: expectInteger(h.forecastHour, at(hp, "forecastHour"), { min: 0 }),
    validTime: expectTimestamp(h.validTime, at(hp, "validTime")),
    width,
    height,
    bounds: orderedBounds(h.bounds, at(hp, "bounds")),
    units: expectString(h.units, at(hp, "units"), { maxLength: 40 }),
    scale,
    offset: expectFiniteNumber(h.offset, at(hp, "offset")),
    nodata: expectInteger(h.nodata, at(hp, "nodata"), { min: 0, max: 65535 }),
    valueMin: expectFiniteNumber(h.valueMin, at(hp, "valueMin")),
    valueMax: expectFiniteNumber(h.valueMax, at(hp, "valueMax")),
    sourceUrl: expectString(h.sourceUrl, at(hp, "sourceUrl"), { maxLength: 2000 }),
    attribution: expectString(h.attribution, at(hp, "attribution"), { maxLength: 300 }),
    cells: new Uint16Array(aligned.buffer, aligned.byteOffset, width * height),
  };
}

const RADAR_KEYS = [
  "kind", "product", "label", "units", "bounds", "retrievedAt", "validTime", "updateIntervalS",
  "attribution", "sourceUrl",
] as const;

const RADAR_SITE_KEYS = [...RADAR_KEYS, "siteId"] as const;

function radarLegend(value: unknown, path: string): string | null {
  if (value === undefined || value === null) return null;
  const text = expectString(value, path, { maxLength: 175000 });
  let decoded: string;
  try { decoded = atob(text); }
  catch { throw new ContractError(path, "expected a base64 PNG legend"); }
  parsePng(Uint8Array.from(decoded, (character) => character.charCodeAt(0)), path);
  return `data:image/png;base64,${text}`;
}

/** get_radar_frame: the header and the PNG as a Blob. */
export function parseRadarFrame(value: unknown, path = ""): RadarFrame {
  const { header: h, body } = parseShrf(value, path);
  const hp = at(path, "header");
  expectExactKeys(h, hp, RADAR_KEYS, ["legendPngBase64"]);
  expectOneOf(h.kind, at(hp, "kind"), ["radar"]);
  if (body.length < PNG_SIGNATURE.length || PNG_SIGNATURE.some((b, i) => body[i] !== b)) {
    throw new ContractError(at(path, "body"), "expected a PNG image");
  }
  return {
    product: expectString(h.product, at(hp, "product"), { minLength: 1, maxLength: 40 }),
    legendPng: radarLegend(h.legendPngBase64, at(hp, "legendPngBase64")),
    label: expectString(h.label, at(hp, "label"), { minLength: 1, maxLength: 120 }),
    units: expectString(h.units, at(hp, "units"), { maxLength: 40 }),
    bounds: orderedBounds(h.bounds, at(hp, "bounds")),
    retrievedAt: expectNullable(h.retrievedAt, at(hp, "retrievedAt"), expectTimestamp),
    validTime: expectNullable(h.validTime, at(hp, "validTime"), expectTimestamp),
    updateIntervalS: expectFiniteNumber(h.updateIntervalS, at(hp, "updateIntervalS"), { min: 0 }),
    attribution: expectString(h.attribution, at(hp, "attribution"), { maxLength: 300 }),
    sourceUrl: expectString(h.sourceUrl, at(hp, "sourceUrl"), { maxLength: 2000 }),
    png: new Blob([body.slice()], { type: "image/png" }),
  };
}

/** get_radar_site_frame: the mosaic's shape, plus the serving site. */
export function parseRadarSiteFrame(value: unknown, path = ""): RadarSiteFrame {
  const { header: h, body } = parseShrf(value, path);
  const hp = at(path, "header");
  expectExactKeys(h, hp, RADAR_SITE_KEYS, ["legendPngBase64", "palette", "elevationAngle"]);
  expectOneOf(h.kind, at(hp, "kind"), ["radar-site"]);
  if (body.length < PNG_SIGNATURE.length || PNG_SIGNATURE.some((b, i) => body[i] !== b)) {
    throw new ContractError(at(path, "body"), "expected a PNG image");
  }
  return {
    product: expectString(h.product, at(hp, "product"), { minLength: 1, maxLength: 40 }),
    siteId: expectString(h.siteId, at(hp, "siteId"), { minLength: 1, maxLength: 16 }),
    elevationAngle: h.elevationAngle === undefined || h.elevationAngle === null ? null : expectFiniteNumber(h.elevationAngle, at(hp, "elevationAngle"), { min: -1, max: 90 }),
    legendPng: radarLegend(h.legendPngBase64, at(hp, "legendPngBase64")),
    palette: h.palette === undefined ? null : expectNullable(h.palette, at(hp, "palette"), parsePalette),
    label: expectString(h.label, at(hp, "label"), { minLength: 1, maxLength: 120 }),
    units: expectString(h.units, at(hp, "units"), { maxLength: 40 }),
    bounds: orderedBounds(h.bounds, at(hp, "bounds")),
    retrievedAt: expectNullable(h.retrievedAt, at(hp, "retrievedAt"), expectTimestamp),
    validTime: expectNullable(h.validTime, at(hp, "validTime"), expectTimestamp),
    updateIntervalS: expectFiniteNumber(h.updateIntervalS, at(hp, "updateIntervalS"), { min: 0 }),
    attribution: expectString(h.attribution, at(hp, "attribution"), { maxLength: 300 }),
    sourceUrl: expectString(h.sourceUrl, at(hp, "sourceUrl"), { maxLength: 2000 }),
    png: new Blob([body.slice()], { type: "image/png" }),
  };
}

const SATELLITE_KEYS = [
  "kind", "product", "satellite", "label", "units", "bounds", "requestedTime", "retrievedAt",
  "validTime", "updateIntervalS", "coverageFraction", "nativeResolutionKm", "attribution",
  "sourceUrl",
] as const;

/** get_satellite_frame: the rendered GOES frame and its scan provenance. */
export function parseSatelliteFrame(value: unknown, path = ""): SatelliteFrame {
  const { header: h, body } = parseShrf(value, path);
  const hp = at(path, "header");
  expectExactKeys(h, hp, SATELLITE_KEYS);
  expectOneOf(h.kind, at(hp, "kind"), ["satellite"]);
  if (body.length < PNG_SIGNATURE.length || PNG_SIGNATURE.some((b, i) => body[i] !== b)) {
    throw new ContractError(at(path, "body"), "expected a PNG image");
  }
  return {
    product: expectString(h.product, at(hp, "product"), { minLength: 1, maxLength: 16 }),
    satellite: expectString(h.satellite, at(hp, "satellite"), { minLength: 1, maxLength: 16 }),
    label: expectString(h.label, at(hp, "label"), { minLength: 1, maxLength: 120 }),
    units: expectString(h.units, at(hp, "units"), { maxLength: 40 }),
    bounds: orderedBounds(h.bounds, at(hp, "bounds")),
    requestedTime: expectTimestamp(h.requestedTime, at(hp, "requestedTime")),
    retrievedAt: expectNullable(h.retrievedAt, at(hp, "retrievedAt"), expectTimestamp),
    validTime: expectTimestamp(h.validTime, at(hp, "validTime")),
    updateIntervalS: expectFiniteNumber(h.updateIntervalS, at(hp, "updateIntervalS"), { min: 0 }),
    coverageFraction: expectFiniteNumber(h.coverageFraction, at(hp, "coverageFraction"), { min: 0, max: 1 }),
    nativeResolutionKm: expectFiniteNumber(h.nativeResolutionKm, at(hp, "nativeResolutionKm"), { min: 0 }),
    attribution: expectString(h.attribution, at(hp, "attribution"), { maxLength: 300 }),
    sourceUrl: expectString(h.sourceUrl, at(hp, "sourceUrl"), { maxLength: 2000 }),
    png: new Blob([body.slice()], { type: "image/png" }),
  };
}

// Pure converter from the Qt map's geography (sharpmod/resources/basemap.json)
// to one GeoJSON FeatureCollection per boundary family. No I/O happens here;
// tooling/basemapPlugin.ts reads the file and serves or emits the output.

export const BASEMAP_FAMILIES = ["coastline", "lakes", "countries", "states"] as const;
export type BasemapFamily = (typeof BASEMAP_FAMILIES)[number];

export type LonLat = [number, number];

export interface BasemapFeatureCollection {
  type: "FeatureCollection";
  features: [
    {
      type: "Feature";
      properties: { family: BasemapFamily };
      geometry: { type: "MultiLineString"; coordinates: LonLat[][] };
    },
  ];
}

export type BasemapGeoJson = Record<BasemapFamily, BasemapFeatureCollection>;

/** Raised for any malformed basemap input; the message names the failing path. */
export class BasemapFormatError extends Error {
  override readonly name = "BasemapFormatError";
  constructor(
    readonly path: string,
    readonly reason: string,
  ) {
    super(`basemap.json ${path}: ${reason}`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parsePoint(value: unknown, path: string): LonLat {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new BasemapFormatError(path, "expected a [lon, lat] pair");
  }
  const [lon, lat] = value as unknown[];
  if (typeof lon !== "number" || !Number.isFinite(lon) || lon < -180 || lon > 180) {
    throw new BasemapFormatError(path, "longitude must be a finite number in [-180, 180]");
  }
  if (typeof lat !== "number" || !Number.isFinite(lat) || lat < -90 || lat > 90) {
    throw new BasemapFormatError(path, "latitude must be a finite number in [-90, 90]");
  }
  return [lon, lat];
}

function parseLine(value: unknown, path: string): LonLat[] {
  if (!Array.isArray(value)) {
    throw new BasemapFormatError(path, "expected a list of points");
  }
  if (value.length < 2) {
    throw new BasemapFormatError(path, "a line needs at least two points");
  }
  return value.map((point: unknown, index) => parsePoint(point, `${path}[${String(index)}]`));
}

function parseFamily(value: unknown, family: BasemapFamily): LonLat[][] {
  if (!Array.isArray(value)) {
    throw new BasemapFormatError(family, "expected a list of lines");
  }
  return value.map((line: unknown, index) => parseLine(line, `${family}[${String(index)}]`));
}

/** Validates the parsed basemap.json value and converts every family. */
export function convertBasemap(raw: unknown): BasemapGeoJson {
  if (!isRecord(raw)) {
    throw new BasemapFormatError("(root)", "expected an object");
  }
  const keys = Object.keys(raw).sort();
  const expected = [...BASEMAP_FAMILIES].sort();
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    throw new BasemapFormatError(
      "(root)",
      `expected exactly the keys ${expected.join(", ")}, got ${keys.join(", ") || "none"}`,
    );
  }
  const result: Partial<BasemapGeoJson> = {};
  for (const family of BASEMAP_FAMILIES) {
    result[family] = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: { family },
          geometry: { type: "MultiLineString", coordinates: parseFamily(raw[family], family) },
        },
      ],
    };
  }
  return result as BasemapGeoJson;
}

/** Parses basemap.json text; a JSON syntax error is reported as a BasemapFormatError. */
export function convertBasemapText(text: string): BasemapGeoJson {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    throw new BasemapFormatError("(root)", `not valid JSON (${(error as Error).message})`);
  }
  return convertBasemap(raw);
}

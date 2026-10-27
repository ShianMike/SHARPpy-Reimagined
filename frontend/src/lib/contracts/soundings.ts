// Parsers for the sounding and forecast-model commands (DESIGN-contracts C8):
// strict keys, bounded text, finite numbers, and nothing aliased.
import type {
  AnalysisGroup,
  AnalysisParameter,
  Bounds,
  DataTable,
  ForecastModel,
  ModelCatalog,
  SoundingAnalysis,
  SoundingSummary,
  SoundingProgress,
  SoundingTrends,
  TableColumn,
  TableRow,
  TrendPoint,
  WrfFile,
} from "../../types/soundings";
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

const TEXT = { maxLength: 300 };
const label = (value: unknown, path: string): string => expectString(value, path, { minLength: 1, maxLength: 120 });
const optionalText = (value: unknown, path: string): string | null =>
  expectNullable(value, path, (v, p) => expectString(v, p, TEXT));
const optionalNumber = (value: unknown, path: string): number | null => expectNullable(value, path, expectFiniteNumber);

export function parseSoundingProgress(value: unknown): SoundingProgress {
  const obj = expectObject(value, "progress");
  expectExactKeys(obj, "progress", ["stage", "downloadedBytes", "bytesPerSecond"]);
  const stage = expectOneOf(obj.stage, "progress.stage", ["waiting", "downloading", "decoding", "calculating", "processing"] as const);
  const downloadedBytes = expectInteger(obj.downloadedBytes, "progress.downloadedBytes", { min: 0, max: Number.MAX_SAFE_INTEGER });
  const bytesPerSecond = expectFiniteNumber(obj.bytesPerSecond, "progress.bytesPerSecond", { min: 0, max: Number.MAX_SAFE_INTEGER });
  if (stage !== "downloading" && bytesPerSecond !== 0) throw new ContractError("progress.bytesPerSecond", "speed must be zero outside a download");
  return { stage, downloadedBytes, bytesPerSecond };
}

export function parseWrfFile(value: unknown, path = ""): WrfFile {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["filePath", "fileName", "times", "latitude", "longitude"]);
  const times = expectArray(obj.times, at(path, "times"), expectTimestamp);
  if (times.length === 0) throw new ContractError(path, "expected at least one WRF forecast time");
  return {
    filePath: expectString(obj.filePath, at(path, "filePath"), { minLength: 1, maxLength: 4096 }),
    fileName: expectString(obj.fileName, at(path, "fileName"), { minLength: 1, maxLength: 255 }),
    times,
    latitude: expectFiniteNumber(obj.latitude, at(path, "latitude"), { min: -90, max: 90 }),
    longitude: expectFiniteNumber(obj.longitude, at(path, "longitude"), { min: -180, max: 180 }),
  };
}

export function parseBounds(value: unknown, path: string): Bounds {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["west", "east", "south", "north"]);
  return {
    west: expectFiniteNumber(obj.west, at(path, "west"), { min: -180, max: 180 }),
    east: expectFiniteNumber(obj.east, at(path, "east"), { min: -180, max: 180 }),
    south: expectFiniteNumber(obj.south, at(path, "south"), { min: -90, max: 90 }),
    north: expectFiniteNumber(obj.north, at(path, "north"), { min: -90, max: 90 }),
  };
}

function parsePoint(value: unknown, path: string): readonly [number, number] {
  const pair = expectArray(value, path, expectFiniteNumber);
  const [lon, lat] = pair;
  if (pair.length !== 2 || lon === undefined || lat === undefined || Math.abs(lon) > 180 || Math.abs(lat) > 90) {
    throw new ContractError(path, "expected a [lon, lat] pair in range");
  }
  return [lon, lat];
}

function parseModel(value: unknown, path: string): ForecastModel {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "key", "label", "domain", "bounds", "outline", "cycles", "forecastHours", "maxForecastHours", "defaultForecastHour",
    "gridSpacingKm", "notes", "members", "recentRuns", "suggestedRun", "soundingsAvailable",
  ]);
  const hours = expectArray(obj.forecastHours, at(path, "forecastHours"), (v, p) => expectInteger(v, p, { min: 0 }));
  const limits = expectArray(obj.maxForecastHours, at(path, "maxForecastHours"), (v, p) => expectInteger(v, p, { min: 0 }));
  if (limits.length !== 24 || limits.some((hour) => !hours.includes(hour))) {
    throw new ContractError(at(path, "maxForecastHours"), "expected 24 cycle limits from the published forecast hours");
  }
  const runs = expectArray(obj.recentRuns, at(path, "recentRuns"), expectTimestamp);
  const members = expectArray(obj.members, at(path, "members"), (v, p) =>
    expectString(v, p, { minLength: 1, maxLength: 16 }),
  );
  if (hours.length === 0 || runs.length === 0) {
    throw new ContractError(path, "expected at least one forecast hour and one run");
  }
  if (members.length > 32 || new Set(members).size !== members.length) {
    throw new ContractError(at(path, "members"), "expected at most 32 unique member labels");
  }
  return {
    key: expectString(obj.key, at(path, "key"), { minLength: 1, maxLength: 40 }),
    label: label(obj.label, at(path, "label")),
    soundingsAvailable: expectBoolean(obj.soundingsAvailable, at(path, "soundingsAvailable")),
    domain: expectString(obj.domain, at(path, "domain"), { maxLength: 120 }),
    bounds: parseBounds(obj.bounds, at(path, "bounds")),
    outline: expectArray(obj.outline, at(path, "outline"), parsePoint),
    cycles: expectArray(obj.cycles, at(path, "cycles"), (v, p) => expectInteger(v, p, { min: 0, max: 23 })),
    forecastHours: hours,
    maxForecastHours: limits,
    defaultForecastHour: expectInteger(obj.defaultForecastHour, at(path, "defaultForecastHour"), { min: 0 }),
    gridSpacingKm: optionalNumber(obj.gridSpacingKm, at(path, "gridSpacingKm")),
    notes: expectString(obj.notes, at(path, "notes"), { maxLength: 2000 }),
    members,
    recentRuns: runs,
    suggestedRun: expectTimestamp(obj.suggestedRun, at(path, "suggestedRun")),
  };
}

export function parseModelCatalog(value: unknown, path = ""): ModelCatalog {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["models", "generatedAt"]);
  return {
    models: expectArray(obj.models, at(path, "models"), parseModel),
    generatedAt: expectTimestamp(obj.generatedAt, at(path, "generatedAt")),
  };
}

const SUMMARY_KEYS = [
  "soundingId", "kind", "title", "location", "stationId", "stationName", "latitude", "longitude",
  "validTime", "runTime", "forecastHour", "model", "provider", "providerName", "sourceUrl", "fileName",
  "levelCount", "timeCount",
] as const;

export function parseSoundingSummary(value: unknown, path = ""): SoundingSummary {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, SUMMARY_KEYS, ["times"]);
  const times = obj.times === undefined ? undefined : expectArray(obj.times, at(path, "times"), (value, p) => expectNullable(value, p, expectTimestamp));
  if (times !== undefined && times.length !== obj.timeCount) throw new ContractError(at(path, "times"), "must match timeCount");
  const latitude = optionalNumber(obj.latitude, at(path, "latitude"));
  const longitude = optionalNumber(obj.longitude, at(path, "longitude"));
  if ((latitude !== null && Math.abs(latitude) > 90) || (longitude !== null && Math.abs(longitude) > 180)) {
    throw new ContractError(path, "expected coordinates in range");
  }
  return {
    soundingId: expectString(obj.soundingId, at(path, "soundingId"), { minLength: 1, maxLength: 64 }),
    kind: expectOneOf(obj.kind, at(path, "kind"), ["observed", "model", "era5", "file"]),
    title: expectString(obj.title, at(path, "title"), { minLength: 1, maxLength: 300 }),
    location: optionalText(obj.location, at(path, "location")),
    stationId: optionalText(obj.stationId, at(path, "stationId")),
    stationName: optionalText(obj.stationName, at(path, "stationName")),
    latitude,
    longitude,
    validTime: expectNullable(obj.validTime, at(path, "validTime"), expectTimestamp),
    runTime: expectNullable(obj.runTime, at(path, "runTime"), expectTimestamp),
    forecastHour: expectNullable(obj.forecastHour, at(path, "forecastHour"), (v, p) => expectInteger(v, p, { min: 0 })),
    model: optionalText(obj.model, at(path, "model")),
    provider: optionalText(obj.provider, at(path, "provider")),
    providerName: optionalText(obj.providerName, at(path, "providerName")),
    sourceUrl: expectNullable(obj.sourceUrl, at(path, "sourceUrl"), (v, p) => expectString(v, p, { maxLength: 2000 })),
    fileName: optionalText(obj.fileName, at(path, "fileName")),
    levelCount: expectInteger(obj.levelCount, at(path, "levelCount"), { min: 0 }),
    timeCount: expectInteger(obj.timeCount, at(path, "timeCount"), { min: 0 }),
    ...(times === undefined ? {} : { times }),
  };
}

function parseColumn(value: unknown, path: string): TableColumn {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["key", "label", "units", "decimals"]);
  return {
    key: label(obj.key, at(path, "key")),
    label: label(obj.label, at(path, "label")),
    units: expectString(obj.units, at(path, "units"), { maxLength: 20 }),
    decimals: expectInteger(obj.decimals, at(path, "decimals"), { min: 0, max: 6 }),
  };
}

function parseTable(value: unknown, path: string): DataTable {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["title", "columns", "rows"]);
  const columns = expectArray(obj.columns, at(path, "columns"), parseColumn);
  const rows = expectArray(obj.rows, at(path, "rows"), (item, rowPath): TableRow => {
    const row = expectObject(item, rowPath);
    expectExactKeys(row, rowPath, ["key", "label", "short", "values"]);
    const values = expectArray(row.values, at(rowPath, "values"), optionalNumber);
    if (values.length !== columns.length) {
      throw new ContractError(at(rowPath, "values"), `expected ${String(columns.length)} values`);
    }
    return {
      key: label(row.key, at(rowPath, "key")),
      label: label(row.label, at(rowPath, "label")),
      short: expectString(row.short, at(rowPath, "short"), { maxLength: 12 }),
      values,
    };
  });
  return { title: label(obj.title, at(path, "title")), columns, rows };
}

function parseParameter(value: unknown, path: string): AnalysisParameter {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["key", "label", "units", "decimals", "notable", "value"]);
  return {
    key: label(obj.key, at(path, "key")),
    label: label(obj.label, at(path, "label")),
    units: expectString(obj.units, at(path, "units"), { maxLength: 20 }),
    decimals: expectInteger(obj.decimals, at(path, "decimals"), { min: 0, max: 6 }),
    notable: expectOneOf(obj.notable, at(path, "notable"), ["high", "low"]),
    value: optionalNumber(obj.value, at(path, "value")),
  };
}

function parseGroup(value: unknown, path: string): AnalysisGroup {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["name", "parameters"]);
  return {
    name: label(obj.name, at(path, "name")),
    parameters: expectArray(obj.parameters, at(path, "parameters"), parseParameter),
  };
}

export function parseSoundingAnalysis(value: unknown, path = ""): SoundingAnalysis {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["soundingId", "validTime", "watchType", "hazard", "parcelTable", "motionTable", "groups"]);
  return {
    soundingId: expectString(obj.soundingId, at(path, "soundingId"), { minLength: 1, maxLength: 64 }),
    validTime: expectNullable(obj.validTime, at(path, "validTime"), expectTimestamp),
    watchType: expectNullable(obj.watchType, at(path, "watchType"), (v, p) => expectString(v, p, { maxLength: 60 })),
    hazard: expectString(obj.hazard, at(path, "hazard"), { minLength: 1, maxLength: 60 }),
    parcelTable: parseTable(obj.parcelTable, at(path, "parcelTable")),
    motionTable: parseTable(obj.motionTable, at(path, "motionTable")),
    groups: expectArray(obj.groups, at(path, "groups"), parseGroup),
  };
}

function parseTrendPoint(columns: number) {
  return (value: unknown, path: string): TrendPoint => {
    const obj = expectObject(value, path);
    expectExactKeys(obj, path, ["time", "values", "note"]);
    const values = expectArray(obj.values, at(path, "values"), optionalNumber);
    if (values.length !== columns) {
      throw new ContractError(at(path, "values"), `expected ${String(columns)} values`);
    }
    return {
      time: expectNullable(obj.time, at(path, "time"), expectTimestamp),
      values,
      note: expectNullable(obj.note, at(path, "note"), (v, p) => expectString(v, p, { maxLength: 200 })),
    };
  };
}

export function parseSoundingTrends(value: unknown, path = ""): SoundingTrends {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["soundingId", "title", "columns", "points", "truncated"]);
  const columns = expectArray(obj.columns, at(path, "columns"), parseColumn);
  return {
    soundingId: expectString(obj.soundingId, at(path, "soundingId"), { minLength: 1, maxLength: 64 }),
    title: label(obj.title, at(path, "title")),
    columns,
    points: expectArray(obj.points, at(path, "points"), parseTrendPoint(columns.length)),
    truncated: expectBoolean(obj.truncated, at(path, "truncated")),
  };
}

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

/** The render_sounding response: raw PNG bytes (an ArrayBuffer from Tauri). */
export function parsePng(value: unknown, path = ""): Blob {
  let bytes: Uint8Array;
  if (value instanceof ArrayBuffer) {
    bytes = new Uint8Array(value);
  } else if (value instanceof Uint8Array) {
    bytes = value;
  } else if (Array.isArray(value) && value.every((b) => typeof b === "number")) {
    bytes = Uint8Array.from(value);
  } else {
    throw new ContractError(path, "expected PNG bytes");
  }
  if (bytes.length < PNG_SIGNATURE.length || PNG_SIGNATURE.some((b, i) => bytes[i] !== b)) {
    throw new ContractError(path, "expected a PNG image");
  }
  return new Blob([bytes.slice()], { type: "image/png" });
}

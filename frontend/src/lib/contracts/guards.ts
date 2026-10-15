// Hand-written runtime validation helpers (DESIGN-contracts C8). Each throws
// ContractError {path, reason}; parsers build new objects and never alias input.

export class ContractError extends Error {
  override readonly name = "ContractError";
  constructor(
    readonly path: string,
    readonly reason: string,
  ) {
    super(`${path === "" ? "(root)" : path}: ${reason}`);
  }
}

export type JsonObject = Readonly<Record<string, unknown>>;

/** Joins a parent path and a key: "stations[3]" + "latitude". */
export function at(path: string, key: string | number): string {
  if (typeof key === "number") {
    return `${path}[${String(key)}]`;
  }
  return path === "" ? key : `${path}.${key}`;
}

function describe(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value)) {
    return "an array";
  }
  switch (typeof value) {
    case "string":
      return JSON.stringify(value.length > 40 ? `${value.slice(0, 40)}…` : value);
    case "number":
    case "boolean":
      return String(value);
    case "undefined":
      return "nothing";
    case "object":
      return "an object";
    default:
      return typeof value;
  }
}

function fail(path: string, expected: string, value: unknown): never {
  throw new ContractError(path, `expected ${expected}, got ${describe(value)}`);
}

export function isPlainObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function expectObject(value: unknown, path: string): JsonObject {
  if (!isPlainObject(value)) {
    fail(path, "an object", value);
  }
  return value;
}

/** Rejects missing required keys and any key that is neither required nor optional. */
export function expectExactKeys(
  obj: JsonObject,
  path: string,
  required: readonly string[],
  optional: readonly string[] = [],
): void {
  for (const key of required) {
    if (!Object.hasOwn(obj, key)) {
      throw new ContractError(at(path, key), "is required");
    }
  }
  for (const key of Object.keys(obj)) {
    if (!required.includes(key) && !optional.includes(key)) {
      throw new ContractError(at(path, key), "is not part of the contract");
    }
  }
}

export interface LengthRange {
  minLength?: number;
  maxLength?: number;
}

export function expectString(value: unknown, path: string, range: LengthRange = {}): string {
  if (typeof value !== "string") {
    fail(path, "a string", value);
  }
  const { minLength = 0, maxLength = Number.POSITIVE_INFINITY } = range;
  if (value.length < minLength || value.length > maxLength) {
    const bound = Number.isFinite(maxLength) ? `${String(minLength)} to ${String(maxLength)}` : `at least ${String(minLength)}`;
    fail(path, `a string of ${bound} characters`, value);
  }
  return value;
}

export interface NumberRange {
  min?: number;
  max?: number;
}

function rangeText(range: NumberRange): string {
  const { min, max } = range;
  if (min !== undefined && max !== undefined) {
    return ` in [${String(min)}, ${String(max)}]`;
  }
  if (min !== undefined) {
    return ` of at least ${String(min)}`;
  }
  return max !== undefined ? ` of at most ${String(max)}` : "";
}

export function expectFiniteNumber(value: unknown, path: string, range: NumberRange = {}): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    (range.min !== undefined && value < range.min) ||
    (range.max !== undefined && value > range.max)
  ) {
    fail(path, `a finite number${rangeText(range)}`, value);
  }
  return value;
}

export function expectInteger(value: unknown, path: string, range: NumberRange = {}): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    (range.min !== undefined && value < range.min) ||
    (range.max !== undefined && value > range.max)
  ) {
    fail(path, `an integer${rangeText(range)}`, value);
  }
  return value;
}

export function expectBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    fail(path, "a boolean", value);
  }
  return value;
}

export function expectOneOf<const T extends string | number>(value: unknown, path: string, allowed: readonly T[]): T {
  if (!(allowed as readonly unknown[]).includes(value)) {
    fail(path, `one of ${allowed.map((item) => JSON.stringify(item)).join(", ")}`, value);
  }
  return value as T;
}

export function expectArray<T>(value: unknown, path: string, parseItem: (item: unknown, path: string) => T): T[] {
  if (!Array.isArray(value)) {
    fail(path, "an array", value);
  }
  return value.map((item: unknown, index) => parseItem(item, at(path, index)));
}

export function expectNullable<T>(value: unknown, path: string, parse: (value: unknown, path: string) => T): T | null {
  return value === null ? null : parse(value, path);
}

const TIMESTAMP = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/;

/** RFC 3339 UTC with whole seconds, the form dto::rfc3339_utc_now() writes (C3). */
export function expectTimestamp(value: unknown, path: string): string {
  if (typeof value !== "string" || !TIMESTAMP.test(value) || Number.isNaN(Date.parse(value))) {
    fail(path, "an RFC 3339 UTC timestamp with whole seconds", value);
  }
  return value;
}

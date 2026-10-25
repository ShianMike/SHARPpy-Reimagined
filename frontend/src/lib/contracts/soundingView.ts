import type { SoundingView, ViewEdit } from "../../types/soundingView";
import { at, ContractError, expectArray, expectExactKeys, expectFiniteNumber, expectInteger, expectObject, expectOneOf, expectString } from "./guards";
import { parsePng } from "./soundings";

const text = (value: unknown, path: string): string => expectString(value, path, { minLength: 1, maxLength: 300 });
const number = (value: unknown, path: string): number => expectFiniteNumber(value, path);
const coordinate = (value: unknown, path: string): number => expectFiniteNumber(value, path, { min: 0, max: 1 });
const nullableNumber = (value: unknown, path: string): number | null => value === null ? null : number(value, path);

function parseInspection(value: unknown): SoundingView["inspection"] {
  const obj = expectObject(value, "inspection");
  const raw = expectObject(obj.bounds, "inspection.bounds");
  const bounds = { left: coordinate(raw.left, "inspection.bounds.left"), top: coordinate(raw.top, "inspection.bounds.top"),
    right: coordinate(raw.right, "inspection.bounds.right"), bottom: coordinate(raw.bottom, "inspection.bounds.bottom") };
  if (bounds.left >= bounds.right || bounds.top >= bounds.bottom) throw new ContractError("inspection.bounds", "must contain a plot area");
  const levels = expectArray(obj.levels, "inspection.levels", (value, path) => {
    const level = expectObject(value, path);
    const wind = level.hodograph === null ? null : expectObject(level.hodograph, at(path, "hodograph"));
    return { index: expectInteger(level.index, at(path, "index"), { min: 0, max: 10000 }),
      y: expectFiniteNumber(level.y, at(path, "y"), { min: bounds.top, max: bounds.bottom }),
      pressure: expectFiniteNumber(level.pressure, at(path, "pressure"), { min: Number.MIN_VALUE }),
      height: nullableNumber(level.height, at(path, "height")), tmpc: nullableNumber(level.tmpc, at(path, "tmpc")),
      dwpc: nullableNumber(level.dwpc, at(path, "dwpc")),
      windDirection: level.windDirection === null ? null : expectFiniteNumber(level.windDirection, at(path, "windDirection"), { min: 0, max: 360 }),
      windSpeed: level.windSpeed === null ? null : expectFiniteNumber(level.windSpeed, at(path, "windSpeed"), { min: 0 }),
      hodograph: wind === null ? null : { x: coordinate(wind.x, at(path, "hodograph.x")), y: coordinate(wind.y, at(path, "hodograph.y")) },
    };
  });
  if (levels.length > 10001 || new Set(levels.map((level) => level.index)).size !== levels.length) throw new ContractError("inspection.levels", "must contain at most 10001 unique levels");
  return { bounds, levels: levels.sort((a, b) => b.y - a.y) };
}

export function parseViewEdits(value: unknown, path: string): ViewEdit[] {
  return expectArray(value, path, (value, path) => {
    const edit = expectObject(value, path);
    expectExactKeys(edit, path, ["index", "values"]);
    const fields = expectObject(edit.values, at(path, "values"));
    return { index: expectInteger(edit.index, at(path, "index"), { min: -2, max: 10000 }),
      values: Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, number(value, at(path, key))])) };
  });
}

export function parseSoundingView(value: unknown): SoundingView {
  const obj = expectObject(value, "view");
  expectExactKeys(obj, "view", ["image", "width", "height", "points", "edits", "options", "selection", "inspection"]);
  const encoded = expectString(obj.image, "image", { maxLength: 8 * 1024 * 1024 });
  const selection = expectObject(obj.selection, "selection");
  return {
    image: parsePng(Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0))),
    width: expectInteger(obj.width, "width", { min: 1, max: 20000 }),
    height: expectInteger(obj.height, "height", { min: 1, max: 20000 }),
    edits: parseViewEdits(obj.edits, "edits"),
    inspection: parseInspection(obj.inspection),
    selection: { chart: text(selection.chart, "selection.chart"), panel: text(selection.panel, "selection.panel") },
    points: expectArray(obj.points, "points", (value, path) => {
      const point = expectObject(value, path);
      return { id: text(point.id, at(path, "id")), label: text(point.label, at(path, "label")),
        x: expectFiniteNumber(point.x, at(path, "x"), { min: 0, max: 1 }),
        y: expectFiniteNumber(point.y, at(path, "y"), { min: 0, max: 1 }),
        values: expectArray(point.values, at(path, "values"), (value, path) => {
          const field = expectObject(value, path);
          return { key: text(field.key, at(path, "key")), label: text(field.label, at(path, "label")),
            value: number(field.value, path), original: nullableNumber(field.original, at(path, "original")),
            min: number(field.min, path), max: number(field.max, path), unit: text(field.unit, path) };
        }) };
    }),
    options: expectArray(obj.options, "options", (value, path) => {
      const option = expectObject(value, path);
      return { key: expectOneOf(option.key, at(path, "key"), ["chart", "panel"] as const), label: text(option.label, path),
        choices: expectArray(option.choices, path, (value, path) => {
          const choice = expectObject(value, path);
          return { key: text(choice.key, path), label: text(choice.label, path) };
        }) };
    }),
  };
}

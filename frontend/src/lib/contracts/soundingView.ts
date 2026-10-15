import type { SoundingView, ViewEdit } from "../../types/soundingView";
import { at, expectArray, expectExactKeys, expectFiniteNumber, expectInteger, expectObject, expectOneOf, expectString } from "./guards";
import { parsePng } from "./soundings";

const text = (value: unknown, path: string): string => expectString(value, path, { minLength: 1, maxLength: 300 });
const number = (value: unknown, path: string): number => expectFiniteNumber(value, path);

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
  expectExactKeys(obj, "view", ["image", "width", "height", "points", "edits", "options", "selection"]);
  const encoded = expectString(obj.image, "image", { maxLength: 8 * 1024 * 1024 });
  const selection = expectObject(obj.selection, "selection");
  return {
    image: parsePng(Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0))),
    width: expectInteger(obj.width, "width", { min: 1, max: 20000 }),
    height: expectInteger(obj.height, "height", { min: 1, max: 20000 }),
    edits: parseViewEdits(obj.edits, "edits"),
    selection: { chart: text(selection.chart, "selection.chart"), panel: text(selection.panel, "selection.panel") },
    points: expectArray(obj.points, "points", (value, path) => {
      const point = expectObject(value, path);
      return { id: text(point.id, at(path, "id")), label: text(point.label, at(path, "label")),
        x: expectFiniteNumber(point.x, at(path, "x"), { min: 0, max: 1 }),
        y: expectFiniteNumber(point.y, at(path, "y"), { min: 0, max: 1 }),
        values: expectArray(point.values, at(path, "values"), (value, path) => {
          const field = expectObject(value, path);
          return { key: text(field.key, at(path, "key")), label: text(field.label, at(path, "label")),
            value: number(field.value, path), min: number(field.min, path), max: number(field.max, path), unit: text(field.unit, path) };
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

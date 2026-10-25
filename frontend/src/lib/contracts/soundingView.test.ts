import viewExample from "@contracts/public/soundings/view.example.json";
import { expect, it } from "vitest";
import { ContractError } from "./guards";
import { parseSoundingView } from "./soundingView";

const fixture = () => ({ ...structuredClone(viewExample), width: 3260, height: 2198,
  image: btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13)) });

it("parses native inspection levels without changing source values or the fixture", () => {
  const source = fixture();
  const parsed = parseSoundingView(source);
  expect(parsed.inspection).toEqual(source.inspection);
  expect(parsed.inspection.levels).not.toBe(source.inspection.levels);
  const level = parsed.inspection.levels.find((level) => level.index === 6);
  expect(level).toMatchObject({ pressure: 850, height: 1512.6800537109375,
    tmpc: 19.1776123046875, dwpc: 17.66229248046875 });
  const wind = parsed.points.find((point) => point.id === "wind:6");
  expect(level?.hodograph).toEqual({ x: wind?.x, y: wind?.y });
  expect(wind?.values.every((field) => field.original === field.value)).toBe(true);
  const missing = fixture();
  Object.assign(missing.inspection.levels[0] ?? {}, { height: null, tmpc: null, dwpc: null, windDirection: null, windSpeed: null, hodograph: null });
  expect(parseSoundingView(missing).inspection.levels[0]).toMatchObject({ tmpc: null, hodograph: null });
});

it("accepts missing original values and rejects invalid baselines", () => {
  const source = fixture();
  const field = source.points[0]?.values[0];
  if (field === undefined) throw new Error("The fixture needs an editing target");
  Object.assign(field, { original: null });
  expect(parseSoundingView(source).points[0]?.values[0]?.original).toBeNull();
  for (const original of [undefined, Infinity, "warm"]) {
    Object.assign(field, { original });
    expect(() => parseSoundingView(source)).toThrow(ContractError);
  }
});

it.each([
  { y: NaN }, { y: 1 }, { pressure: 0 }, { height: Infinity }, { tmpc: "warm" },
  { dwpc: -Infinity }, { windDirection: 361 }, { windSpeed: -1 }, { hodograph: { x: -1, y: 0.5 } },
])("rejects invalid level metadata %j", (change) => {
  const source = fixture();
  Object.assign(source.inspection.levels[0] ?? {}, change);
  expect(() => parseSoundingView(source)).toThrow(ContractError);
});

it("rejects empty plot bounds and duplicate levels", () => {
  const source = fixture();
  source.inspection.bounds.right = source.inspection.bounds.left;
  expect(() => parseSoundingView(source)).toThrow(ContractError);
  const repeated = fixture();
  const first = repeated.inspection.levels[0];
  if (first === undefined) throw new Error("The fixture needs profile levels");
  repeated.inspection.levels.push({ ...first });
  expect(() => parseSoundingView(repeated)).toThrow(ContractError);
});

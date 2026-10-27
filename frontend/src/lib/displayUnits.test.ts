import { expect, it } from "vitest";
import { convertUnit, DEFAULT_UNITS, displayReading, displayUnit, formatUnit, parseUnitPreferences } from "./displayUnits";

it.each([
  ["m^2/s^2", "m²/s²"], ["m^3/s^3", "m³/s³"], ["kg m^-2 s^-1", "kg m⁻² s⁻¹"],
  ["m^10 s^+2", "m¹⁰ s⁺²"], ["m²/s²", "m²/s²"], ["m^0.5", "m^0.5"],
  ["°C/km", "°C/km"], ["J/kg", "J/kg"], ["", ""],
])("formats %s as %s without changing its value", (source, expected) => {
  expect(formatUnit(source)).toBe(expected);
  expect(displayUnit(source, DEFAULT_UNITS)).toBe(expected);
  expect(displayUnit(source, DEFAULT_UNITS, "fixed")).toBe(expected);
  expect(convertUnit(125, source, expected)).toBe(125);
});

it("honours fixed quantities and scales temperature differences without an offset", () => {
  const preferences = { temperature: "°F", wind: "mph" } as const;
  expect(displayUnit("m/s", preferences, "fixed")).toBe("m/s");
  expect(convertUnit(20, "m/s", "mph", "fixed")).toBe(20);
  expect(displayUnit("°C", preferences, "temperature-difference")).toBe("°F");
  expect(convertUnit(0, "°C", "°F", "temperature-difference")).toBe(0);
  expect(convertUnit(-5, "°C", "°F", "temperature-difference")).toBe(-9);
  expect(convertUnit(-9, "°F", "°C", "temperature-difference")).toBe(-5);
});

it.each([[32, "°F", "°C", 0], [-40, "°C", "°F", -40], [20, "°C", "°F", 68],
  [20, "kt", "km/h", 37.04], [20, "kt", "mph", 23.0155889605], [20, "kt", "m/s", 10.2888888889],
  [-12.5, "m/s", "kt", -24.2980561555]] as const)("converts %s %s to %s and back", (value, source, target, expected) => {
  const converted = convertUnit(value, source, target);
  expect(converted).toBeCloseTo(expected, 8);
  expect(convertUnit(converted, target, source)).toBeCloseTo(value, 10);
});

it("validates saved fields independently, defaults old preferences, and leaves other quantities alone", () => {
  for (const saved of [undefined, null, {}, [], "°C"]) expect(parseUnitPreferences(saved)).toEqual(DEFAULT_UNITS);
  expect(parseUnitPreferences({ temperature: "°C", wind: "unknown" })).toEqual({ temperature: "°C", wind: "kt" });
  expect(parseUnitPreferences({ temperature: "invalid", wind: "mph" })).toEqual({ temperature: "source", wind: "mph" });
  for (const source of ["J/kg", "°C/km", "%", "dBZ", "", "__proto__"]) {
    expect(displayUnit(source, { temperature: "°F", wind: "mph" })).toBe(source);
    expect(convertUnit(12, source, "mph")).toBe(12);
  }
  expect(displayUnit("°C", DEFAULT_UNITS)).toBe("°C");
  expect(displayUnit("°F", DEFAULT_UNITS)).toBe("°F");
});

it("changes only complete reading tokens, preserving missing values, direction, timestamps and compound units", () => {
  const text = "2026-10-04 21:00Z · -20.0 °C · missing · 270° at 20 kt, gusting 28 kt · 6 °C/km";
  expect(displayReading(text, { temperature: "°F", wind: "km/h" }))
    .toBe("2026-10-04 21:00Z · -4 °F · missing · 270° at 37 km/h, gusting 51.9 km/h · 6 °C/km");
  expect(displayReading(text, DEFAULT_UNITS)).toBe(text);
});

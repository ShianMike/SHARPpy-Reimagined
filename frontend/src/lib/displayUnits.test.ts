import { expect, it } from "vitest";
import { convertUnit, DEFAULT_UNITS, displayReading, displayUnit, parseUnitPreferences } from "./displayUnits";

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
  for (const source of ["J/kg", "°C/km", "m^2/s^2", "%", "dBZ", "", "__proto__"]) {
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

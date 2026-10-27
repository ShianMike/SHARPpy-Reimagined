import type { Palette } from "../types/map";

export const TEMPERATURE_UNITS = ["source", "°C", "°F"] as const;
export const WIND_UNITS = ["kt", "km/h", "mph", "m/s"] as const;
export interface UnitPreferences {
  temperature: (typeof TEMPERATURE_UNITS)[number];
  wind: (typeof WIND_UNITS)[number];
}
export const DEFAULT_UNITS: UnitPreferences = { temperature: "source", wind: "kt" };

const SUPERSCRIPTS: Readonly<Record<string, string>> = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻" };

export function formatUnit(unit: string): string {
  return unit.replace(/\^([+-]?\d+)(?![\d.])/gu, (_token: string, exponent: string) => Array.from(exponent, (character) => SUPERSCRIPTS[character]).join(""));
}

export function parseUnitPreferences(saved: unknown): UnitPreferences {
  const value = saved !== null && typeof saved === "object" ? saved as Record<string, unknown> : {};
  return {
    temperature: TEMPERATURE_UNITS.find((unit) => unit === value.temperature) ?? DEFAULT_UNITS.temperature,
    wind: WIND_UNITS.find((unit) => unit === value.wind) ?? DEFAULT_UNITS.wind,
  };
}

export function displayUnit(source: string, preferences: UnitPreferences, display: Palette["display"] = "auto"): string {
  if (display === "fixed") return formatUnit(source);
  if (source === "°C" || source === "°F") return preferences.temperature === "source" ? source : preferences.temperature;
  return WIND_UNITS.some((unit) => unit === source) ? preferences.wind : formatUnit(source);
}

const WIND_IN_MS = new Map<string, number>([["kt", 1852 / 3600], ["km/h", 1 / 3.6], ["mph", 0.44704], ["m/s", 1]]);

/** Display conversion only: source values, colour thresholds and calculations stay in their original units. */
export function convertUnit(value: number, source: string, target: string, display: Palette["display"] = "auto"): number {
  if (source === target || display === "fixed") return value;
  if (display === "temperature-difference") {
    if (source === "°C" && target === "°F") return value * 9 / 5;
    if (source === "°F" && target === "°C") return value * 5 / 9;
    return value;
  }
  if (source === "°C" && target === "°F") return value * 9 / 5 + 32;
  if (source === "°F" && target === "°C") return (value - 32) * 5 / 9;
  const from = WIND_IN_MS.get(source), to = WIND_IN_MS.get(target);
  return from === undefined || to === undefined ? value : value * from / to;
}

/** Only explicit numeric unit tokens change; timestamps, directions, missing values and narrative stay intact. */
export function displayReading(text: string, preferences: UnitPreferences): string {
  return text.replace(/(-?\d+(?:\.\d+)?) (°C|°F|kt|km\/h|mph|m\/s)(?![\w/])/gu, (token: string, raw: string, source: string) => {
    const target = displayUnit(source, preferences);
    return target === source ? token : `${String(Number(convertUnit(Number(raw), source, target).toFixed(1)))} ${target}`;
  });
}

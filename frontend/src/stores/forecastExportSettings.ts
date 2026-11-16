export const FORECAST_EXPORT_SIZES = [
  { value: 1280, label: "1280 × 720 · HD" }, { value: 1920, label: "1920 × 1080 · Full HD" },
  { value: 2560, label: "2560 × 1440 · QHD" }, { value: 3840, label: "3840 × 2160 · UHD" },
];
export const FORECAST_EXPORT_DELAYS = [
  { value: 250, label: "0.25 seconds" }, { value: 500, label: "0.5 seconds" },
  { value: 750, label: "0.75 seconds" }, { value: 1000, label: "1 second" },
];
export interface ForecastExportSettings {
  scope: "panel" | "layout";
  width: number;
  delay: number;
}
export const DEFAULT_FORECAST_EXPORT_SETTINGS: ForecastExportSettings = { scope: "panel", width: 1920, delay: 750 };

/** Persisted choices are validated once when UI preferences are restored. */
export function parseForecastExportSettings(value: unknown): ForecastExportSettings {
  const saved = value !== null && typeof value === "object" ? value as Record<string, unknown> : {};
  return {
    scope: saved.scope === "layout" ? "layout" : "panel",
    width: FORECAST_EXPORT_SIZES.find(option => option.value === saved.width)?.value ?? DEFAULT_FORECAST_EXPORT_SETTINGS.width,
    delay: FORECAST_EXPORT_DELAYS.find(option => option.value === saved.delay)?.value ?? DEFAULT_FORECAST_EXPORT_SETTINGS.delay,
  };
}

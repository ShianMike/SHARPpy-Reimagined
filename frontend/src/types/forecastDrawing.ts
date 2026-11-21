import type { OutlookArea } from "./map";

export const FORECAST_PRODUCTS = ["cat", "torn", "hail", "wind"] as const;
export type ForecastProduct = (typeof FORECAST_PRODUCTS)[number];

export const FORECAST_PRODUCT_LABELS: Record<ForecastProduct, string> = {
  cat: "Categorical Risk",
  torn: "Tornado Risk",
  hail: "Hail Risk",
  wind: "Wind Risk",
};

export const FORECAST_LEVELS: Record<ForecastProduct, readonly number[]> = {
  cat: [0, 1, 2, 3, 4, 5],
  torn: [2, 5, 10, 15, 30, 45, 60],
  hail: [5, 15, 30, 45, 60],
  wind: [5, 15, 30, 45, 60, 75, 90],
};
export const FORECAST_CATEGORIES = ["General Thunderstorms", "Marginal", "Slight", "Enhanced", "Moderate", "High"] as const;
export const FORECAST_HATCH_LEVELS: Record<ForecastProduct, readonly number[]> = {
  cat: [], torn: [1, 2, 3], hail: [1, 2], wind: [1, 2, 3],
};
export const MAX_FORECAST_POLYGONS = 64;
export const MAX_FORECAST_VERTICES = 256;

export const UNOFFICIAL_FORECAST_TITLE = "Unofficial user forecast";
export const FORECAST_OUTLOOK_DAYS = [1, 2, 3, 4, 5, 6, 7, 8] as const;
export const MAX_FORECAST_AUTHOR_LENGTH = 64;

export type ForecastOutlookDay = (typeof FORECAST_OUTLOOK_DAYS)[number];

export interface ForecastDrawingDetails {
  readonly author: string;
  readonly day: ForecastOutlookDay | null;
}

export type ForecastPoint = readonly [longitude: number, latitude: number];

export interface ForecastPolygon {
  id: string;
  level: number;
  hatch: boolean;
  points: readonly ForecastPoint[];
  sourceId?: string;
}

export interface GeneratedForecastContour {
  sourceId: string;
  level: number;
  points: readonly ForecastPoint[];
}

export interface ForecastDrawingResult {
  areas: readonly OutlookArea[];
  clippedHatches: readonly string[];
  hiddenHatches: readonly string[];
  generatedContours: readonly GeneratedForecastContour[];
}

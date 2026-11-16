export interface ViewEdit { index: number; values: Record<string, number> }
export interface ViewField { key: string; label: string; value: number; original: number | null; unit: string; min: number; max: number }
export interface ViewPoint { id: string; label: string; x: number; y: number; values: ViewField[] }
export interface InspectionLevel {
  index: number; y: number; pressure: number; height: number | null;
  tmpc: number | null; dwpc: number | null; windDirection: number | null; windSpeed: number | null;
  hodograph: { x: number; y: number } | null;
}
export interface ViewOptions { chart?: string; panel?: string }
export type SoundingMode = "severe" | "winter";
export interface ViewRequest extends ViewOptions {
  mode?: SoundingMode;
  timeIndex?: number;
  animationRange?: number;
  edits: ViewEdit[];
  drag?: { target: string; x: number; y: number };
}
export interface SoundingView {
  image: Blob;
  width: number;
  height: number;
  edits: ViewEdit[];
  points: ViewPoint[];
  inspection: { bounds: { left: number; top: number; right: number; bottom: number }; levels: InspectionLevel[] };
  /** Normalized native inset bounds and the resolved sounding location, when available. */
  locatorBounds?: { left: number; top: number; right: number; bottom: number; location?: string };
  selection: Required<ViewOptions>;
  options: { key: keyof ViewOptions; label: string; choices: { key: string; label: string }[] }[];
}

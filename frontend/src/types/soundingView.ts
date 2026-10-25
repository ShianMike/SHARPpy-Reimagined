export interface ViewEdit { index: number; values: Record<string, number> }
export interface ViewField { key: string; label: string; value: number; original: number | null; unit: string; min: number; max: number }
export interface ViewPoint { id: string; label: string; x: number; y: number; values: ViewField[] }
export interface InspectionLevel {
  index: number; y: number; pressure: number; height: number | null;
  tmpc: number | null; dwpc: number | null; windDirection: number | null; windSpeed: number | null;
  hodograph: { x: number; y: number } | null;
}
export interface ViewOptions { chart?: string; panel?: string }
export interface ViewRequest extends ViewOptions {
  timeIndex?: number;
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
  selection: Required<ViewOptions>;
  options: { key: keyof ViewOptions; label: string; choices: { key: string; label: string }[] }[];
}

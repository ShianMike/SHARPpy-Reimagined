export interface ViewEdit { index: number; values: Record<string, number> }
export interface ViewField { key: string; label: string; value: number; unit: string; min: number; max: number }
export interface ViewPoint { id: string; label: string; x: number; y: number; values: ViewField[] }
export interface ViewOptions { chart?: string; panel?: string }
export interface ViewRequest extends ViewOptions {
  edits: ViewEdit[];
  drag?: { target: string; x: number; y: number };
}
export interface SoundingView {
  image: Blob;
  width: number;
  height: number;
  edits: ViewEdit[];
  points: ViewPoint[];
  selection: Required<ViewOptions>;
  options: { key: keyof ViewOptions; label: string; choices: { key: string; label: string }[] }[];
}

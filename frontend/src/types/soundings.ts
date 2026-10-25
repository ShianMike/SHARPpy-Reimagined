// Sounding and forecast-model DTOs (src-tauri/src/dto/{models,soundings,analysis}.rs),
// pinned by src-tauri/contracts/public/soundings/. Every quantity arrives
// labelled by sharpmod; the frontend only lays the values out.

export interface Bounds {
  west: number;
  east: number;
  south: number;
  north: number;
}

export interface SoundingProgress {
  stage: "waiting" | "downloading" | "decoding" | "calculating" | "processing";
  downloadedBytes: number;
  bytesPerSecond: number;
}

export interface ForecastModel {
  key: string;
  label: string;
  domain: string;
  bounds: Bounds;
  /** The real grid perimeter as [lon, lat] points; empty when only bounds are known. */
  outline: readonly (readonly [number, number])[];
  cycles: readonly number[];
  forecastHours: readonly number[];
  /** Last forecast hour by UTC cycle, 00Z to 23Z; zero for unpublished cycles. */
  maxForecastHours: readonly number[];
  defaultForecastHour: number;
  gridSpacingKm: number | null;
  notes: string;
  /** Newest first, RFC 3339 UTC. */
  recentRuns: readonly string[];
  /** The newest run likely to be published. */
  suggestedRun: string;
  /**
   * The ensemble member labels this model accepts, control member first;
   * empty for a deterministic model. sharpmod's own names: the frontend
   * requests members by position in this list.
   */
  members: readonly string[];
}

export interface ModelCatalog {
  models: readonly ForecastModel[];
  generatedAt: string;
}

export interface ModelStatus {
  model: string;
  provider: string;
  runTime: string;
  forecastHour: number;
  state: "available" | "partial" | "unavailable" | "unknown" | "configured";
  message: string;
  checkedAt: string;
}

export const OBSERVED_PROVIDERS = ["auto", "uwyo", "iem", "igra"] as const;
export type ObservedProvider = (typeof OBSERVED_PROVIDERS)[number];

export type OpenSoundingRequest =
  | { kind: "observed"; stationId: string; time: string; provider: ObservedProvider }
  | {
      kind: "model";
      model: string;
      runTime: string | null;
      forecastHour: number;
      latitude: number;
      longitude: number;
      location: string | null;
    }
  | { kind: "era5"; time: string; latitude: number; longitude: number; location: string | null }
  | { kind: "wrf"; filePath: string; time: string; latitude: number; longitude: number }
  | { kind: "file"; fileName: string; contentBase64: string };

// Raw WRF datasets become portable point soundings in the File collection.
export type SoundingKind = Exclude<OpenSoundingRequest["kind"], "wrf">;

export interface WrfFile {
  filePath: string;
  fileName: string;
  times: readonly string[];
  latitude: number;
  longitude: number;
}

export interface SoundingSummary {
  soundingId: string;
  kind: SoundingKind;
  title: string;
  location: string | null;
  stationId: string | null;
  stationName: string | null;
  latitude: number | null;
  longitude: number | null;
  validTime: string | null;
  runTime: string | null;
  forecastHour: number | null;
  model: string | null;
  provider: string | null;
  providerName: string | null;
  sourceUrl: string | null;
  fileName: string | null;
  levelCount: number;
  timeCount: number;
  /** Every file time in collection order, including undated entries. */
  times?: readonly (string | null)[];
}

export interface TableColumn {
  key: string;
  label: string;
  units: string;
  decimals: number;
}

export interface TableRow {
  key: string;
  label: string;
  short: string;
  /** One value per column; null when sharpmod has no value. */
  values: readonly (number | null)[];
}

export interface DataTable {
  title: string;
  columns: readonly TableColumn[];
  rows: readonly TableRow[];
}

export interface AnalysisParameter {
  key: string;
  label: string;
  units: string;
  decimals: number;
  /** Which end of the range is notable. */
  notable: "high" | "low";
  value: number | null;
}

export interface AnalysisGroup {
  name: string;
  parameters: readonly AnalysisParameter[];
}

export interface SoundingAnalysis {
  soundingId: string;
  validTime: string | null;
  /** The SPC possible hazard type the sounding image shows. */
  watchType: string | null;
  /** sharpmod's reformulated hazard classification. */
  hazard: string;
  parcelTable: DataTable;
  motionTable: DataTable;
  groups: readonly AnalysisGroup[];
}

export interface TrendPoint {
  /** RFC 3339 UTC valid time; null when the file has no date for it. */
  time: string | null;
  /** One value per column; null when sharpmod has no value. */
  values: readonly (number | null)[];
  /** Why the point has no values, when it has none. */
  note: string | null;
}

/** The legacy Trends metrics at every valid time of a forecast file. */
export interface SoundingTrends {
  soundingId: string;
  title: string;
  columns: readonly TableColumn[];
  points: readonly TrendPoint[];
  truncated: boolean;
}

/** The renderer's ascent choices: sharpmod.rendering.cli.PARCEL_TYPES without USER. */
export const RENDER_CHOICES = ["SFC", "ML", "FCST", "MU", "EFF"] as const;
export type RenderChoice = (typeof RENDER_CHOICES)[number];
export type ImageMode = "hd" | "uhd";

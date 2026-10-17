// Surface station-plot records (DESIGN-contracts C8): where a station is, what
// it reported, and the display slots its station model draws. The reading pair
// is position-keyed (upper, lower); numeric temperatures support display-unit
// conversion without parsing the provider's description.

export interface StaffSpec {
  /** Screen degrees of the staff tip from east, y down: toward the wind's source. */
  angleDeg: number;
  pennants: number;
  barbs: number;
  halves: number;
}

export type PlotAvailability = "available" | "unavailable" | "unknown";
export type PlotWindState = "available" | "calm" | "missing" | "direction-missing";

export interface SurfacePlot {
  id: string;
  longitude: number;
  latitude: number;
  /** The observation time; null for a station checked without a match. */
  valid: string | null;
  /** Minutes from the requested time; null with `valid`. */
  offsetMin: number | null;
  stale: boolean;
  availability: PlotAvailability;
  windState: PlotWindState;
  /** The wind mark spec; present exactly when `windState` is "available". */
  staff: StaffSpec | null;
  temperatureC: number | null;
  dewpointC: number | null;
  /** The plot's upper reading, at its own whole-degree precision. */
  upperText: string;
  /** The plot's lower reading, at its own whole-degree precision. */
  lowerText: string;
  label: string;
  description: string;
}

export interface SurfacePlotCounts {
  matched: number;
  stale: number;
  noObservation: number;
  failed: number;
  nearby: number;
  queried: number;
  unqueried: number;
}

export interface SurfacePlots {
  validTime: string;
  counts: SurfacePlotCounts;
  plots: SurfacePlot[];
}

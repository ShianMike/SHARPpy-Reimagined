// Ensemble summary DTOs (src-tauri/src/dto/ensemble.rs), pinned by
// src-tauri/contracts/public/soundings/ensemble.example.json. Every quantity
// arrives labelled by sharpmod; the frontend only lays the values out.

/** One requested member that did not produce a loaded profile. */
export interface MemberFailure {
  member: string;
  status: "failed" | "cancelled" | "unknown";
  reason: string | null;
}

/**
 * One diagnostic's spread across the usable members: its own denominator, the
 * 10th, middle, and 90th percentiles, and the lowest to highest member. All
 * five statistics are present together, or all absent with a zero count.
 */
export interface MetricSpread {
  key: string;
  label: string;
  units: string;
  decimals: number;
  count: number;
  p10: number | null;
  median: number | null;
  p90: number | null;
  minimum: number | null;
  maximum: number | null;
}

/** One requested member's own row: its outcome and one value per metric. */
export interface MemberSample {
  member: string;
  outcome: "completed" | "failed" | "cancelled" | "unavailable";
  reason: string | null;
  values: readonly (number | null)[];
}

/** What one ensemble run's members said at one valid time. */
export interface EnsembleSummary {
  validTime: string | null;
  reason: string | null;
  requestedMembers: readonly string[];
  loadedMembers: readonly string[];
  failures: readonly MemberFailure[];
  metrics: readonly MetricSpread[];
  samples: readonly MemberSample[];
}

/** The request `get_ensemble_summary` takes; `members` names 2 to 32 labels. */
export interface EnsembleSummaryRequest {
  model: string;
  latitude: number;
  longitude: number;
  forecastHour: number;
  runTime: string | null;
  location: string | null;
  members: readonly string[];
}

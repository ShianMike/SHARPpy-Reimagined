// Threshold explorer DTOs (src-tauri/src/dto/thresholds.rs and
// dto/threshold_catalog.rs), pinned by
// src-tauri/contracts/public/soundings/threshold.example.json and
// threshold-metrics.json. Every label, unit, state phrase, and sentence
// arrives from sharpmod; the frontend only lays the values out.

/** One condition as the form sends it; the bridge fills the units. */
export interface ThresholdConditionInput {
  metric: string;
  operator: ThresholdOperator;
  value: number;
  upper: number | null;
}

/** The `definition` argument of `evaluate_threshold`. */
export interface ThresholdDefinitionInput {
  name: string;
  conditions: readonly ThresholdConditionInput[];
}

/** The comparison rules, in the order every control lists them. */
export type ThresholdOperator = ">" | ">=" | "<" | "<=" | "==" | "between";

/** One condition as the evaluator returns it, with the registry units. */
export interface ThresholdCondition {
  metric: string;
  operator: ThresholdOperator;
  value: number;
  upper: number | null;
  units: string;
}

/** The rule the evaluator tested. */
export interface ThresholdDefinition {
  name: string;
  conditions: readonly ThresholdCondition[];
}

/** Exactly one of these applies to every requested member. */
export type ThresholdState =
  | "qualifying"
  | "nonqualifying"
  | "unusable"
  | "no_profile"
  | "failed"
  | "not_loaded"
  | "not_evaluated";

/** Where one requested member landed, with the values the rule read. */
export interface ThresholdMember {
  member: string;
  loaded: boolean;
  usable: boolean;
  satisfied: boolean | null;
  state: ThresholdState;
  stateLabel: string;
  /** One value per definition diagnostic, keyed by its registry key. */
  values: Readonly<Record<string, number | null>>;
  reason: string | null;
}

/**
 * One definition's verdicts across the requested members: the counts with
 * their denominators, the agreement and coverage sentences, and one row per
 * member.
 */
export interface ThresholdEvaluation {
  definition: ThresholdDefinition;
  definitionSummary: string;
  validTime: string | null;
  requestedMemberCount: number;
  loadedMemberCount: number;
  usableMemberCount: number;
  qualifyingMemberCount: number;
  nonqualifyingMemberCount: number;
  loadedUnusableMemberCount: number;
  notLoadedMemberCount: number;
  resolvedMemberCount: number;
  notEvaluatedMemberCount: number;
  agreementFraction: number | null;
  agreement: string;
  requestedCoverage: number | null;
  coverage: string;
  reconciles: boolean;
  cancelled: boolean;
  label: string;
  members: readonly ThresholdMember[];
}

/** One registry diagnostic with its numeric-control bounds. */
export interface ThresholdMetric {
  key: string;
  label: string;
  units: string;
  displayLabel: string;
  decimals: number;
  minimum: number;
  maximum: number;
  step: number;
  suggested: number;
  guidance: string;
}

/** The threshold form's catalog: diagnostics, rules, and starting metrics. */
export interface ThresholdMetrics {
  metrics: readonly ThresholdMetric[];
  operators: readonly ThresholdOperator[];
  defaultMetrics: readonly string[];
}

/** The same verdicts `export_threshold_csv` writes, as download text. */
export interface ThresholdCsv {
  fileName: string;
  csv: string;
}

/** The ensemble fields every threshold operation shares. */
export interface ThresholdRequest {
  model: string;
  latitude: number;
  longitude: number;
  forecastHour: number;
  runTime: string | null;
  location: string | null;
  members: readonly string[];
}

/** `evaluate_threshold` / `export_threshold_csv` arguments. */
export interface ThresholdEvaluationRequest extends ThresholdRequest {
  definition: ThresholdDefinitionInput;
}

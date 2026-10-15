// Parser for threshold evaluations (DESIGN-contracts C8): strict keys, bounded
// text, finite numbers, and nothing aliased. Every rule mirrors the Rust
// validator in `dto/thresholds.rs`, which runs first. The form's catalog parser
// lives in `thresholdCatalog.ts`.
import type {
  ThresholdCondition,
  ThresholdDefinition,
  ThresholdEvaluation,
  ThresholdEvaluationRequest,
  ThresholdMember,
  ThresholdOperator,
  ThresholdState,
} from "../../types/thresholds";
import {
  ContractError,
  at,
  expectArray,
  expectBoolean,
  expectExactKeys,
  expectFiniteNumber,
  expectInteger,
  expectNullable,
  expectObject,
  expectOneOf,
  expectString,
  type JsonObject,
} from "./guards";

/** Generously beyond every numeric-control bound (dto/thresholds.rs). */
export const VALUE_BOUND = 1e9;
export const MAX_CONDITIONS = 3;
const OPERATORS: readonly ThresholdOperator[] = [">", ">=", "<", "<=", "==", "between"];
const STATES: readonly ThresholdState[] = [
  "qualifying",
  "nonqualifying",
  "unusable",
  "no_profile",
  "failed",
  "not_loaded",
  "not_evaluated",
];
const ASCII = /^[\x20-\x7E]*$/;

const optionalNumber = (value: unknown, path: string): number | null =>
  expectNullable(value, path, (v, p) => expectFiniteNumber(v, p, { min: -VALUE_BOUND, max: VALUE_BOUND }));

function parseCondition(value: unknown, path: string): ThresholdCondition {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["metric", "operator", "value", "upper", "units"]);
  const condition: ThresholdCondition = {
    metric: expectString(obj.metric, at(path, "metric"), { minLength: 1, maxLength: 32 }),
    operator: expectOneOf(obj.operator, at(path, "operator"), OPERATORS),
    value: expectFiniteNumber(obj.value, at(path, "value"), { min: -VALUE_BOUND, max: VALUE_BOUND }),
    upper: optionalNumber(obj.upper, at(path, "upper")),
    units: expectString(obj.units, at(path, "units"), { maxLength: 40 }),
  };
  if (condition.operator === "between") {
    if (condition.upper === null || condition.upper < condition.value) {
      throw new ContractError(path, "between requires an upper value at least as large");
    }
  } else if (condition.upper !== null) {
    throw new ContractError(path, "upper is only valid with the between operator");
  }
  return condition;
}

function parseDefinition(value: unknown, path: string): ThresholdDefinition {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["name", "conditions"]);
  const definition: ThresholdDefinition = {
    name: expectString(obj.name, at(path, "name"), { minLength: 1, maxLength: 120 }),
    conditions: expectArray(obj.conditions, at(path, "conditions"), parseCondition),
  };
  if (definition.conditions.length < 1 || definition.conditions.length > MAX_CONDITIONS) {
    throw new ContractError(path, "conditions must hold 1 to 3 conditions");
  }
  const metrics = definition.conditions.map((condition) => condition.metric);
  if (new Set(metrics).size !== metrics.length) {
    throw new ContractError(path, "definition metrics must be unique");
  }
  return definition;
}

function parseValues(value: unknown, path: string, wanted: ReadonlySet<string>): Record<string, number | null> {
  const obj: JsonObject = expectObject(value, path);
  const entries: Record<string, number | null> = {};
  for (const [key, item] of Object.entries(obj)) {
    if (key.length < 1 || key.length > 32) {
      throw new ContractError(at(path, key), "a values key must be 1 to 32 characters");
    }
    entries[key] = optionalNumber(item, at(path, key));
  }
  const got = new Set(Object.keys(entries));
  if (got.size !== wanted.size || [...wanted].some((key) => !got.has(key))) {
    throw new ContractError(path, "a member row carries the definition's diagnostics");
  }
  return entries;
}

function parseMember(value: unknown, path: string, wanted: ReadonlySet<string>): ThresholdMember {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "member", "loaded", "usable", "satisfied", "state", "stateLabel", "values", "reason",
  ]);
  const member: ThresholdMember = {
    member: expectString(obj.member, at(path, "member"), { minLength: 1, maxLength: 16 }),
    loaded: expectBoolean(obj.loaded, at(path, "loaded")),
    usable: expectBoolean(obj.usable, at(path, "usable")),
    satisfied: expectNullable(obj.satisfied, at(path, "satisfied"), expectBoolean),
    state: expectOneOf(obj.state, at(path, "state"), STATES),
    stateLabel: expectString(obj.stateLabel, at(path, "stateLabel"), { minLength: 1, maxLength: 80 }),
    values: parseValues(obj.values, at(path, "values"), wanted),
    reason: expectNullable(obj.reason, at(path, "reason"), (v, p) => expectString(v, p, { maxLength: 300 })),
  };
  const verdict = member.state === "qualifying" || member.state === "nonqualifying";
  if (member.usable !== verdict) {
    throw new ContractError(path, "only a qualifying or nonqualifying member is usable");
  }
  if ((member.satisfied !== null) !== member.usable) {
    throw new ContractError(path, "a member reports a verdict exactly when it is usable");
  }
  if (member.state === "not_loaded" && (member.loaded || member.usable)) {
    throw new ContractError(path, "a member that never loaded reports neither");
  }
  return member;
}

/** The ensemble fields and the definition, as one command's arguments. */
export function thresholdArgs(
  request: ThresholdEvaluationRequest,
): Readonly<Record<string, unknown>> {
  return {
    model: request.model,
    latitude: request.latitude,
    longitude: request.longitude,
    forecastHour: request.forecastHour,
    runTime: request.runTime,
    location: request.location,
    members: request.members,
    definition: {
      name: request.definition.name,
      conditions: request.definition.conditions.map((condition) => ({
        metric: condition.metric,
        operator: condition.operator,
        value: condition.value,
        upper: condition.upper,
      })),
    },
  };
}

/** Strict parse of one `thresholds.evaluate` result. */
export function parseThresholdEvaluation(value: unknown, path = "evaluation"): ThresholdEvaluation {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "definition", "definitionSummary", "validTime",
    "requestedMemberCount", "loadedMemberCount", "usableMemberCount",
    "qualifyingMemberCount", "nonqualifyingMemberCount",
    "loadedUnusableMemberCount", "notLoadedMemberCount",
    "resolvedMemberCount", "notEvaluatedMemberCount",
    "agreementFraction", "agreement", "requestedCoverage", "coverage",
    "reconciles", "cancelled", "label", "members",
  ]);
  const definition = parseDefinition(obj.definition, at(path, "definition"));
  const wanted = new Set(definition.conditions.map((condition) => condition.metric));
  const members = expectArray(obj.members, at(path, "members"), (item, p) => parseMember(item, p, wanted));
  const evaluation: ThresholdEvaluation = {
    definition,
    definitionSummary: expectString(obj.definitionSummary, at(path, "definitionSummary"), { minLength: 1, maxLength: 300 }),
    validTime: expectNullable(obj.validTime, at(path, "validTime"), (v, p) => {
      const text = expectString(v, p, { minLength: 10, maxLength: 40 });
      if (!ASCII.test(text)) {
        throw new ContractError(p, "a valid time must be ASCII");
      }
      return text;
    }),
    requestedMemberCount: expectInteger(obj.requestedMemberCount, at(path, "requestedMemberCount"), { min: 2, max: 32 }),
    loadedMemberCount: expectInteger(obj.loadedMemberCount, at(path, "loadedMemberCount"), { min: 0, max: 32 }),
    usableMemberCount: expectInteger(obj.usableMemberCount, at(path, "usableMemberCount"), { min: 0, max: 32 }),
    qualifyingMemberCount: expectInteger(obj.qualifyingMemberCount, at(path, "qualifyingMemberCount"), { min: 0, max: 32 }),
    nonqualifyingMemberCount: expectInteger(obj.nonqualifyingMemberCount, at(path, "nonqualifyingMemberCount"), { min: 0, max: 32 }),
    loadedUnusableMemberCount: expectInteger(obj.loadedUnusableMemberCount, at(path, "loadedUnusableMemberCount"), { min: 0, max: 32 }),
    notLoadedMemberCount: expectInteger(obj.notLoadedMemberCount, at(path, "notLoadedMemberCount"), { min: 0, max: 32 }),
    resolvedMemberCount: expectInteger(obj.resolvedMemberCount, at(path, "resolvedMemberCount"), { min: 0, max: 32 }),
    notEvaluatedMemberCount: expectInteger(obj.notEvaluatedMemberCount, at(path, "notEvaluatedMemberCount"), { min: 0, max: 32 }),
    agreementFraction: optionalNumber(obj.agreementFraction, at(path, "agreementFraction")),
    agreement: expectString(obj.agreement, at(path, "agreement"), { minLength: 1, maxLength: 40 }),
    requestedCoverage: optionalNumber(obj.requestedCoverage, at(path, "requestedCoverage")),
    coverage: expectString(obj.coverage, at(path, "coverage"), { minLength: 1, maxLength: 40 }),
    reconciles: expectBoolean(obj.reconciles, at(path, "reconciles")),
    cancelled: expectBoolean(obj.cancelled, at(path, "cancelled")),
    label: expectString(obj.label, at(path, "label"), { minLength: 1, maxLength: 120 }),
    members,
  };
  validateTallies(evaluation, path);
  return evaluation;
}

/** The counts are the member rows' own tallies, and the groups partition. */
function validateTallies(evaluation: ThresholdEvaluation, path: string): void {
  const { members } = evaluation;
  const count = (keep: (member: ThresholdMember) => boolean): number =>
    members.filter(keep).length;
  const tallies = {
    loadedMemberCount: count((m) => m.loaded),
    usableMemberCount: count((m) => m.usable),
    qualifyingMemberCount: count((m) => m.satisfied === true),
    nonqualifyingMemberCount: count((m) => m.satisfied === false),
    loadedUnusableMemberCount: count((m) => m.loaded && !m.usable),
    notLoadedMemberCount: count((m) => m.state === "not_loaded"),
    notEvaluatedMemberCount: count((m) => m.state === "not_evaluated"),
  };
  for (const [key, tally] of Object.entries(tallies)) {
    if (evaluation[key as keyof typeof tallies] !== tally) {
      throw new ContractError(at(path, key), "a reported count disagrees with the member rows");
    }
  }
  if (evaluation.qualifyingMemberCount + evaluation.nonqualifyingMemberCount !== evaluation.usableMemberCount) {
    throw new ContractError(path, "the usable members split into qualifying and nonqualifying");
  }
  if (evaluation.usableMemberCount + evaluation.loadedUnusableMemberCount !== evaluation.loadedMemberCount) {
    throw new ContractError(path, "the loaded members split into usable and unusable");
  }
  if (
    evaluation.qualifyingMemberCount + evaluation.nonqualifyingMemberCount
      + evaluation.loadedUnusableMemberCount + evaluation.notLoadedMemberCount
    !== evaluation.requestedMemberCount
  ) {
    throw new ContractError(path, "the four terminal groups must partition the request");
  }
  if (evaluation.resolvedMemberCount + evaluation.notEvaluatedMemberCount !== evaluation.requestedMemberCount) {
    throw new ContractError(path, "resolved and not-evaluated must partition the request");
  }
  if (!evaluation.reconciles) {
    throw new ContractError(at(path, "reconciles"), "the reported counts must reconcile");
  }
  if ((evaluation.agreementFraction !== null) !== (evaluation.usableMemberCount > 0)) {
    throw new ContractError(at(path, "agreementFraction"), "an agreement fraction exists exactly when usable members do");
  }
  if (evaluation.agreementFraction !== null && (evaluation.agreementFraction < 0 || evaluation.agreementFraction > 1)) {
    throw new ContractError(at(path, "agreementFraction"), "an agreement fraction must lie within [0, 1]");
  }
  if ((evaluation.requestedCoverage !== null) !== (evaluation.requestedMemberCount > 0)) {
    throw new ContractError(at(path, "requestedCoverage"), "a coverage exists exactly when members were requested");
  }
  if (evaluation.requestedCoverage !== null && (evaluation.requestedCoverage < 0 || evaluation.requestedCoverage > 1)) {
    throw new ContractError(at(path, "requestedCoverage"), "a coverage must lie within [0, 1]");
  }
}

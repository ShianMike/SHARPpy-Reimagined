// Parser for the ensemble summary (DESIGN-contracts C8): strict keys, bounded
// text, finite numbers, and nothing aliased. Every rule mirrors the Rust
// validator in `dto/ensemble.rs`, which runs first.
import type {
  EnsembleSummary,
  MemberFailure,
  MemberSample,
  MetricSpread,
} from "../../types/ensemble";
import {
  ContractError,
  at,
  expectArray,
  expectExactKeys,
  expectFiniteNumber,
  expectInteger,
  expectNullable,
  expectObject,
  expectOneOf,
  expectString,
  expectTimestamp,
} from "./guards";

const MEMBER = { minLength: 1, maxLength: 16 };
const REASON = { maxLength: 300 };
const optionalNumber = (value: unknown, path: string): number | null =>
  expectNullable(value, path, expectFiniteNumber);

function parseFailure(value: unknown, path: string): MemberFailure {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["member", "status", "reason"]);
  return {
    member: expectString(obj.member, at(path, "member"), MEMBER),
    status: expectOneOf(obj.status, at(path, "status"), ["failed", "cancelled", "unknown"]),
    reason: expectNullable(obj.reason, at(path, "reason"), (v, p) => expectString(v, p, REASON)),
  };
}

function parseSpread(value: unknown, path: string): MetricSpread {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "key", "label", "units", "decimals", "count",
    "p10", "median", "p90", "minimum", "maximum",
  ]);
  const spread: MetricSpread = {
    key: expectString(obj.key, at(path, "key"), { minLength: 1, maxLength: 16 }),
    label: expectString(obj.label, at(path, "label"), { minLength: 1, maxLength: 120 }),
    units: expectString(obj.units, at(path, "units"), { maxLength: 40 }),
    decimals: expectInteger(obj.decimals, at(path, "decimals"), { min: 0, max: 6 }),
    count: expectInteger(obj.count, at(path, "count"), { min: 0, max: 32 }),
    p10: optionalNumber(obj.p10, at(path, "p10")),
    median: optionalNumber(obj.median, at(path, "median")),
    p90: optionalNumber(obj.p90, at(path, "p90")),
    minimum: optionalNumber(obj.minimum, at(path, "minimum")),
    maximum: optionalNumber(obj.maximum, at(path, "maximum")),
  };
  const stats = [spread.p10, spread.median, spread.p90, spread.minimum, spread.maximum];
  if (spread.count === 0) {
    if (stats.some((entry) => entry !== null)) {
      throw new ContractError(path, "a spread with no members carries no statistics");
    }
    return spread;
  }
  if (stats.some((entry) => entry === null)) {
    throw new ContractError(path, "a spread with members carries every statistic");
  }
  const [p10, median, p90, minimum, maximum] = stats as [number, number, number, number, number];
  if (!(minimum <= p10 && p10 <= median && median <= p90 && p90 <= maximum)) {
    throw new ContractError(path, "a spread must read minimum to p10 to median to p90 to maximum");
  }
  return spread;
}

function parseSample(value: unknown, path: string, metrics: number): MemberSample {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["member", "outcome", "reason", "values"]);
  const sample: MemberSample = {
    member: expectString(obj.member, at(path, "member"), MEMBER),
    outcome: expectOneOf(obj.outcome, at(path, "outcome"), [
      "completed", "failed", "cancelled", "unavailable",
    ]),
    reason: expectNullable(obj.reason, at(path, "reason"), (v, p) => expectString(v, p, REASON)),
    values: expectArray(obj.values, at(path, "values"), (v, p) =>
      expectNullable(v, p, expectFiniteNumber),
    ),
  };
  if (sample.values.length !== metrics) {
    throw new ContractError(at(path, "values"), "a member row carries one value per metric");
  }
  if (sample.outcome !== "completed" && sample.values.some((entry) => entry !== null)) {
    throw new ContractError(at(path, "values"), "only a completed member reports values");
  }
  return sample;
}

function unique(values: readonly string[], path: string): void {
  if (new Set(values).size !== values.length) {
    throw new ContractError(path, "must be unique");
  }
}

export function parseEnsembleSummary(value: unknown, path = ""): EnsembleSummary {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "validTime", "reason", "requestedMembers", "loadedMembers", "failures", "metrics", "samples",
  ]);
  const member = (v: unknown, p: string): string => expectString(v, p, MEMBER);
  const validTime = expectNullable(obj.validTime, at(path, "validTime"), expectTimestamp);
  const reason = expectNullable(obj.reason, at(path, "reason"), (v, p) => expectString(v, p, REASON));
  const requestedMembers = expectArray(obj.requestedMembers, at(path, "requestedMembers"), member);
  const loadedMembers = expectArray(obj.loadedMembers, at(path, "loadedMembers"), member);
  const failures = expectArray(obj.failures, at(path, "failures"), parseFailure);
  const metrics = expectArray(obj.metrics, at(path, "metrics"), parseSpread);
  if (requestedMembers.length < 2 || requestedMembers.length > 32) {
    throw new ContractError(at(path, "requestedMembers"), "must hold 2 to 32 member labels");
  }
  if (metrics.length < 1 || metrics.length > 16) {
    throw new ContractError(at(path, "metrics"), "must hold 1 to 16 rows");
  }
  unique(requestedMembers, at(path, "requestedMembers"));
  unique(loadedMembers, at(path, "loadedMembers"));
  unique(failures.map((failure) => failure.member), at(path, "failures"));
  unique(metrics.map((metric) => metric.key), at(path, "metrics"));

  const requested = new Set(requestedMembers);
  for (const name of loadedMembers) {
    if (!requested.has(name)) {
      throw new ContractError(at(path, "loadedMembers"), "a loaded member must belong to the request");
    }
  }
  const resolved = new Set(loadedMembers);
  for (const failure of failures) {
    if (!requested.has(failure.member)) {
      throw new ContractError(at(path, "failures"), "a failed member must belong to the request");
    }
    if (resolved.has(failure.member)) {
      throw new ContractError(at(path, "failures"), "a member cannot be both loaded and unavailable");
    }
    resolved.add(failure.member);
  }
  if (resolved.size !== requested.size) {
    throw new ContractError(at(path, "failures"), "every requested member is loaded or carries one status");
  }

  const samples = expectArray(obj.samples, at(path, "samples"), (v, p) =>
    parseSample(v, p, metrics.length),
  );
  unique(samples.map((sample) => sample.member), at(path, "samples"));
  if (samples.length !== requestedMembers.length
    || samples.some((sample) => !requested.has(sample.member))) {
    throw new ContractError(at(path, "samples"), "the member rows must cover the request");
  }
  // The counts are the samples' own tallies: each spread says how many
  // members reported it, and the member rows must agree.
  metrics.forEach((metric, index) => {
    const reported = samples.filter(
      (sample) => sample.outcome === "completed" && sample.values[index] !== null,
    ).length;
    if (metric.count !== reported) {
      throw new ContractError(at(at(path, "metrics"), index), "count disagrees with the member rows");
    }
  });
  return { validTime, reason, requestedMembers, loadedMembers, failures, metrics, samples };
}

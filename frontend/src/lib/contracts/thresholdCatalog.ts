// Parser for the threshold form's catalog and the CSV download
// (DESIGN-contracts C8). Every rule mirrors the Rust validator in
// `dto/threshold_catalog.rs`, which runs first.
import type { ThresholdCsv, ThresholdMetric, ThresholdMetrics, ThresholdOperator } from "../../types/thresholds";
import {
  ContractError,
  at,
  expectArray,
  expectExactKeys,
  expectFiniteNumber,
  expectInteger,
  expectObject,
  expectOneOf,
  expectString,
} from "./guards";
import { MAX_CONDITIONS } from "./thresholds";

const OPERATORS: readonly ThresholdOperator[] = [">", ">=", "<", "<=", "==", "between"];

function parseMetric(value: unknown, path: string): ThresholdMetric {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, [
    "key", "label", "units", "displayLabel", "decimals",
    "minimum", "maximum", "step", "suggested", "guidance",
  ]);
  const metric: ThresholdMetric = {
    key: expectString(obj.key, at(path, "key"), { minLength: 1, maxLength: 32 }),
    label: expectString(obj.label, at(path, "label"), { minLength: 1, maxLength: 120 }),
    units: expectString(obj.units, at(path, "units"), { maxLength: 40 }),
    displayLabel: expectString(obj.displayLabel, at(path, "displayLabel"), { minLength: 1, maxLength: 160 }),
    decimals: expectInteger(obj.decimals, at(path, "decimals"), { min: 0, max: 6 }),
    minimum: expectFiniteNumber(obj.minimum, at(path, "minimum")),
    maximum: expectFiniteNumber(obj.maximum, at(path, "maximum")),
    step: expectFiniteNumber(obj.step, at(path, "step")),
    suggested: expectFiniteNumber(obj.suggested, at(path, "suggested")),
    guidance: expectString(obj.guidance, at(path, "guidance"), { minLength: 1, maxLength: 300 }),
  };
  if (metric.minimum >= metric.maximum) {
    throw new ContractError(path, "a control range must read minimum to maximum");
  }
  if (metric.step <= 0) {
    throw new ContractError(path, "a control step must be positive");
  }
  if (metric.suggested < metric.minimum || metric.suggested > metric.maximum) {
    throw new ContractError(path, "a suggested value must lie within the control range");
  }
  return metric;
}

/** Strict parse of one `thresholds.metrics` catalog. */
export function parseThresholdMetrics(value: unknown, path = "metrics"): ThresholdMetrics {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["metrics", "operators", "defaultMetrics"]);
  const metrics = expectArray(obj.metrics, at(path, "metrics"), parseMetric);
  if (metrics.length < 1 || metrics.length > 64) {
    throw new ContractError(path, "metrics must hold 1 to 64 rows");
  }
  const keys = metrics.map((metric) => metric.key);
  if (new Set(keys).size !== keys.length) {
    throw new ContractError(path, "metrics must be unique");
  }
  const operators = expectArray(obj.operators, at(path, "operators"), (item, p) =>
    expectOneOf(item, p, OPERATORS));
  if (operators.length !== OPERATORS.length || operators.some((op, index) => op !== OPERATORS[index])) {
    throw new ContractError(at(path, "operators"), "operators must list the six comparison rules in order");
  }
  const defaultMetrics = expectArray(obj.defaultMetrics, at(path, "defaultMetrics"), (item, p) =>
    expectString(item, p, { minLength: 1, maxLength: 32 }));
  if (defaultMetrics.length < 1 || defaultMetrics.length > MAX_CONDITIONS) {
    throw new ContractError(path, "defaultMetrics must hold 1 to 3 diagnostics");
  }
  if (new Set(defaultMetrics).size !== defaultMetrics.length) {
    throw new ContractError(path, "defaultMetrics must be unique");
  }
  for (const key of defaultMetrics) {
    if (!keys.includes(key)) {
      throw new ContractError(path, "a default metric must come from the catalog");
    }
  }
  return { metrics, operators, defaultMetrics };
}

/** Strict parse of one `thresholds.export_csv` download. */
export function parseThresholdCsv(value: unknown, path = "csv"): ThresholdCsv {
  const obj = expectObject(value, path);
  expectExactKeys(obj, path, ["fileName", "csv"]);
  const exported: ThresholdCsv = {
    fileName: expectString(obj.fileName, at(path, "fileName"), { minLength: 1, maxLength: 80 }),
    csv: expectString(obj.csv, at(path, "csv"), { minLength: 1, maxLength: 300000 }),
  };
  if (!exported.csv.includes("\n")) {
    throw new ContractError(at(path, "csv"), "csv must carry its rows");
  }
  return exported;
}

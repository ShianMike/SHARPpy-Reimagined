import threshold from "@contracts/public/soundings/threshold.example.json";
import thresholdCsv from "@contracts/public/soundings/threshold-csv.example.json";
import thresholdMetrics from "@contracts/public/soundings/threshold-metrics.json";
import { describe, expect, it } from "vitest";
import { ContractError } from "./guards";
import { parseThresholdEvaluation } from "./thresholds";
import { parseThresholdCsv, parseThresholdMetrics } from "./thresholdCatalog";
import type { ThresholdMember } from "../../types/thresholds";

function edit(value: unknown, change: (copy: Record<string, unknown>) => void): unknown {
  const copy = structuredClone(value) as Record<string, unknown>;
  change(copy);
  return copy;
}

function row(c: Record<string, unknown>, list: string, index: number): Record<string, unknown> {
  return (c[list] as unknown[])[index] as Record<string, unknown>;
}

/** The fixture's own definition metrics; the frontend never spells them. */
function metricsOf(c: Record<string, unknown>): string[] {
  const conditions = (c.definition as Record<string, unknown>).conditions as unknown[];
  return conditions.map((condition) => (condition as Record<string, unknown>).metric as string);
}

describe("threshold evaluation contracts", () => {
  it("parses the exported fixture unchanged", () => {
    expect(parseThresholdEvaluation(threshold)).toEqual(threshold);
    const evaluation = parseThresholdEvaluation(threshold);
    // The rule the evaluator tested, and the sentence naming it.
    expect(evaluation.definition.name).toBeTypeOf("string");
    expect(evaluation.definition.conditions).toHaveLength(2);
    expect(evaluation.definitionSummary.length).toBeGreaterThan(0);
    // The counts partition the request and name their denominators.
    expect(evaluation.requestedMemberCount).toBe(4);
    expect(evaluation.qualifyingMemberCount).toBe(1);
    expect(evaluation.nonqualifyingMemberCount).toBe(2);
    expect(evaluation.notLoadedMemberCount).toBe(1);
    expect(evaluation.reconciles).toBe(true);
    expect(evaluation.cancelled).toBe(false);
    expect(evaluation.agreement).toBe("1/3 (33%)");
    expect(evaluation.coverage).toBe("3/4 (75%)");
    expect(evaluation.label.length).toBeGreaterThan(0);
    // One row per requested member, each in exactly one state with its phrase.
    expect(evaluation.members).toHaveLength(4);
    const winner = evaluation.members[0] as ThresholdMember;
    expect(winner.state).toBe("qualifying");
    expect(winner.stateLabel).toBe("qualifies");
    expect(winner.satisfied).toBe(true);
    expect(Object.keys(winner.values)).toEqual(metricsOf(threshold as Record<string, unknown>));
    const missing = evaluation.members[3] as ThresholdMember;
    expect(missing.state).toBe("not_loaded");
    expect(missing.stateLabel).toBe("not loaded");
    expect(missing.satisfied).toBeNull();
    expect(Object.values(missing.values)).toEqual([null, null]);
  });

  it.each([
    ["counts that disagree with the member rows", (c: Record<string, unknown>) => {
      row(c, "members", 0).loaded = false;
    }],
    ["a partition that does not add up", (c: Record<string, unknown>) => {
      c.nonqualifyingMemberCount = (c.nonqualifyingMemberCount as number) + 1;
    }],
    ["a verdict on an unusable member", (c: Record<string, unknown>) => {
      row(c, "members", 3).satisfied = true;
    }],
    ["a state outside the seven", (c: Record<string, unknown>) => {
      row(c, "members", 0).state = "half";
    }],
    ["a loaded member that never loaded", (c: Record<string, unknown>) => {
      row(c, "members", 3).loaded = true;
    }],
    ["an agreement fraction outside its denominator's range", (c: Record<string, unknown>) => {
      c.agreementFraction = 1.5;
    }],
    ["an upper value outside a between rule", (c: Record<string, unknown>) => {
      const conditions = (c.definition as Record<string, unknown>).conditions as unknown[];
      (conditions[1] as Record<string, unknown>).upper = 1;
    }],
    ["a value for a diagnostic the rule never named", (c: Record<string, unknown>) => {
      const values = row(c, "members", 0).values as Record<string, unknown>;
      values.extra = 1;
    }],
    ["a valid time that is not a time", (c: Record<string, unknown>) => {
      c.validTime = "yesterday";
    }],
    ["a definition with a duplicated metric", (c: Record<string, unknown>) => {
      const conditions = (c.definition as Record<string, unknown>).conditions as unknown[];
      conditions[1] = structuredClone(conditions[0]);
    }],
  ])("refuses %s", (_name, change) => {
    expect(() => parseThresholdEvaluation(edit(threshold, change))).toThrow(ContractError);
  });
});

describe("threshold catalog contracts", () => {
  it("parses the exported fixture unchanged", () => {
    expect(parseThresholdMetrics(thresholdMetrics)).toEqual(thresholdMetrics);
    const catalog = parseThresholdMetrics(thresholdMetrics);
    // The catalog: every registry diagnostic with its own control bounds.
    expect(catalog.metrics.length).toBeGreaterThan(50);
    for (const metric of catalog.metrics) {
      expect(metric.label.length).toBeGreaterThan(0);
      expect(metric.guidance.length).toBeGreaterThan(0);
      expect(metric.minimum).toBeLessThan(metric.maximum);
      expect(metric.suggested).toBeGreaterThanOrEqual(metric.minimum);
      expect(metric.suggested).toBeLessThanOrEqual(metric.maximum);
    }
    expect(catalog.operators).toHaveLength(6);
    expect(catalog.defaultMetrics).toHaveLength(3);
    const keys = catalog.metrics.map((metric) => metric.key);
    for (const key of catalog.defaultMetrics) {
      expect(keys).toContain(key);
    }
  });

  it.each([
    ["a suggested value outside its range", (c: Record<string, unknown>) => {
      row(c, "metrics", 0).suggested = 1e9;
    }],
    ["a truncated operator list", (c: Record<string, unknown>) => {
      c.operators = [">"];
    }],
    ["a default the catalog does not carry", (c: Record<string, unknown>) => {
      c.defaultMetrics = ["not-a-diagnostic"];
    }],
  ])("refuses %s", (_name, change) => {
    expect(() => parseThresholdMetrics(edit(thresholdMetrics, change))).toThrow(ContractError);
  });
});

describe("threshold csv contracts", () => {
  it("parses the exported fixture unchanged", () => {
    expect(parseThresholdCsv(thresholdCsv)).toEqual(thresholdCsv);
    const exported = parseThresholdCsv(thresholdCsv);
    expect(exported.fileName).toBe("ensemble-threshold-results.csv");
    expect(exported.csv.split("\n").length).toBe(6);
  });

  it("refuses a csv with no rows", () => {
    expect(() =>
      parseThresholdCsv(edit(thresholdCsv, (c) => {
        c.csv = "header";
      })),
    ).toThrow(ContractError);
  });
});

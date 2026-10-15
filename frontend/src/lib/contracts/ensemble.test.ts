import ensemble from "@contracts/public/soundings/ensemble.example.json";
import { describe, expect, it } from "vitest";
import { ContractError } from "./guards";
import { parseEnsembleSummary } from "./ensemble";
import type { MetricSpread } from "../../types/ensemble";

function edit(value: unknown, change: (copy: Record<string, unknown>) => void): unknown {
  const copy = structuredClone(value) as Record<string, unknown>;
  change(copy);
  return copy;
}

/** One row of one list of the fixture, mutated by the refusal cases. */
function row(c: Record<string, unknown>, list: string, index: number): Record<string, unknown> {
  return (c[list] as unknown[])[index] as Record<string, unknown>;
}

function column(c: Record<string, unknown>, list: string, index: number): (number | null)[] {
  return row(c, list, index).values as (number | null)[];
}

describe("ensemble summary contracts", () => {
  it("parses the exported fixture unchanged", () => {
    expect(parseEnsembleSummary(ensemble)).toEqual(ensemble);
    const summary = parseEnsembleSummary(ensemble);
    // The ledger: four members asked for, three loaded, one failed to download.
    expect(summary.requestedMembers).toEqual(["c00", "p01", "p02", "p03"]);
    expect(summary.loadedMembers).toEqual(["c00", "p01", "p02"]);
    expect(summary.failures).toEqual([
      { member: "p03", status: "failed", reason: "no GRIB found for member p03" },
    ]);
    expect(summary.validTime).toBe("2026-06-26T00:00:00Z");
    expect(summary.reason).toBeNull();
    // Each diagnostic arrives labelled, with its own usable denominator.
    expect(summary.metrics).toHaveLength(5);
    for (const metric of summary.metrics) {
      expect(metric.label.length).toBeGreaterThan(0);
      expect(metric.units).toBeTypeOf("string");
    }
    const first = summary.metrics[0] as MetricSpread;
    expect(first.count).toBe(3);
    expect(first.p10 ?? 0).toBeLessThanOrEqual(first.median ?? 0);
    expect(first.median ?? 0).toBeLessThanOrEqual(first.p90 ?? 0);
    // Every requested member keeps one row, including the one that failed.
    expect(summary.samples).toHaveLength(4);
    const missing = summary.samples.find((sample) => sample.member === "p03");
    expect(missing?.outcome).toBe("unavailable");
    expect(missing?.values).toEqual([null, null, null, null, null]);
    expect(missing?.reason).toContain("no GRIB found");
  });

  it.each([
    ["a spread that reads backwards", (c: Record<string, unknown>) => {
      const first = row(c, "metrics", 0);
      first.p10 = (first.p90 as number) + 1;
    }],
    ["a count that disagrees with the member rows", (c: Record<string, unknown>) => {
      row(c, "metrics", 0).count = (row(c, "metrics", 0).count as number) + 1;
    }],
    ["statistics on a spread with no members", (c: Record<string, unknown>) => {
      row(c, "metrics", 0).count = 0;
    }],
    ["a member value without its spread", (c: Record<string, unknown>) => {
      row(c, "metrics", 0).p10 = null;
    }],
    ["an unknown member outcome", (c: Record<string, unknown>) => {
      row(c, "samples", 0).outcome = "half";
    }],
    ["a value on a member that did not complete", (c: Record<string, unknown>) => {
      column(c, "samples", 3)[0] = 1;
    }],
    ["a value column without its metric", (c: Record<string, unknown>) => {
      column(c, "samples", 0).push(1);
    }],
    ["a member row claiming loaded and unavailable", (c: Record<string, unknown>) => {
      row(c, "failures", 0).member = "c00";
    }],
    ["a second row for one member", (c: Record<string, unknown>) => {
      (c.requestedMembers as unknown[]).push("p01");
      (c.samples as unknown[]).push((c.samples as unknown[])[0]);
    }],
    ["a loaded member outside the request", (c: Record<string, unknown>) => {
      (c.loadedMembers as unknown[]).push("p77");
    }],
    ["a request with no member rows", (c: Record<string, unknown>) => {
      (c.samples as unknown[]).pop();
    }],
    ["a non-finite value", (c: Record<string, unknown>) => {
      column(c, "samples", 0)[0] = Number.NaN;
    }],
    ["an unknown failure status", (c: Record<string, unknown>) => {
      row(c, "failures", 0).status = "stuck";
    }],
    ["an unbounded reason", (c: Record<string, unknown>) => {
      row(c, "failures", 0).reason = "x".repeat(301);
    }],
    ["an unbounded member label", (c: Record<string, unknown>) => {
      (c.requestedMembers as unknown[])[0] = "c00-c00-c00-c00-c";
    }],
    ["a second metric key", (c: Record<string, unknown>) => {
      (c.metrics as unknown[]).push(row(c, "metrics", 1));
      for (let index = 0; index < (c.samples as unknown[]).length; index += 1) {
        column(c, "samples", index).push(column(c, "samples", index)[1] ?? null);
      }
    }],
    ["an extra key", (c: Record<string, unknown>) => {
      c.gust = 28;
    }],
  ])("rejects %s", (_name, change) => {
    expect(() => parseEnsembleSummary(edit(ensemble, change))).toThrow(ContractError);
  });

  it("rejects the empty and the non-object", () => {
    expect(() => parseEnsembleSummary(null)).toThrow(ContractError);
    expect(() => parseEnsembleSummary("")).toThrow(ContractError);
  });
});

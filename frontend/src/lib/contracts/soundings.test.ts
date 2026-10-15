import analysisExample from "@contracts/public/soundings/analysis.example.json";
import modelCatalog from "@contracts/public/soundings/models.catalog.json";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import summaryMultiTime from "@contracts/public/soundings/summary.multi-time.json";
import trendsExample from "@contracts/public/soundings/trends.example.json";
import { describe, expect, it } from "vitest";
import { ContractError } from "./guards";
import {
  parseModelCatalog,
  parsePng,
  parseSoundingAnalysis,
  parseSoundingSummary,
  parseSoundingTrends,
} from "./soundings";

/** A deep copy that a test may change. */
function edit(value: unknown, change: (copy: Record<string, unknown>) => void): unknown {
  const copy = structuredClone(value) as Record<string, unknown>;
  change(copy);
  return copy;
}

describe("sounding contracts", () => {
  it("parses the exported fixtures without changing them", () => {
    expect(parseModelCatalog(modelCatalog)).toEqual(modelCatalog);
    expect(parseSoundingSummary(summaryExample)).toEqual(summaryExample);
    expect(parseSoundingSummary(summaryMultiTime)).toEqual(summaryMultiTime);
    expect(parseSoundingAnalysis(analysisExample)).toEqual(analysisExample);
    expect(parseSoundingTrends(trendsExample)).toEqual(trendsExample);
  });

  it("keeps the shapes the views rely on", () => {
    const analysis = parseSoundingAnalysis(analysisExample);
    for (const table of [analysis.parcelTable, analysis.motionTable]) {
      expect(table.title.length).toBeGreaterThan(0);
      for (const row of table.rows) {
        expect(row.values).toHaveLength(table.columns.length);
      }
    }
    const trends = parseSoundingTrends(trendsExample);
    expect(trends.points).toHaveLength(parseSoundingSummary(summaryMultiTime).timeCount);
    const hrrr = parseModelCatalog(modelCatalog).models.find((model) => model.key === "hrrr");
    expect(hrrr?.outline.length).toBeGreaterThan(100);
    expect(hrrr?.recentRuns).toContain(hrrr?.suggestedRun);
  });

  it.each([
    ["an extra summary key", () => parseSoundingSummary(edit(summaryExample, (c) => (c.extra = 1)))],
    ["a latitude out of range", () => parseSoundingSummary(edit(summaryExample, (c) => (c.latitude = 91)))],
    ["an unknown kind", () => parseSoundingSummary(edit(summaryExample, (c) => (c.kind = "radar")))],
    [
      "a row with the wrong number of values",
      () =>
        parseSoundingAnalysis(
          edit(analysisExample, (c) => {
            const table = c.parcelTable as { rows: { values: unknown[] }[] };
            table.rows[0]?.values.pop();
          }),
        ),
    ],
    ["a non-finite value", () => parseSoundingAnalysis(edit(analysisExample, (c) => (c.hazard = 3)))],
    [
      "a model with invalid cycle limits",
      () => parseModelCatalog(edit(modelCatalog, (c) => {
        const models = c.models as { maxForecastHours: unknown[] }[];
        models[0]?.maxForecastHours.splice(0, 1);
      })),
    ],
    [
      "a trend point with a value too many",
      () =>
        parseSoundingTrends(
          edit(trendsExample, (c) => {
            (c.points as { values: unknown[] }[])[0]?.values.push(1);
          }),
        ),
    ],
    [
      "a model with no runs",
      () =>
        parseModelCatalog(
          edit(modelCatalog, (c) => {
            const models = c.models as { recentRuns: unknown[] }[];
            if (models[0] !== undefined) {
              models[0].recentRuns = [];
            }
          }),
        ),
    ],
  ])("rejects %s", (_name, parse) => {
    expect(parse).toThrow(ContractError);
  });

  it("accepts PNG bytes in every form Tauri may deliver, and nothing else", () => {
    const png = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13];
    for (const value of [Uint8Array.from(png).buffer, Uint8Array.from(png), png]) {
      const blob = parsePng(value);
      expect(blob.type).toBe("image/png");
      expect(blob.size).toBe(png.length);
    }
    expect(() => parsePng(Uint8Array.from([1, 2, 3]))).toThrow(ContractError);
    expect(() => parsePng("iVBOR")).toThrow(ContractError);
  });
});

import reports from "@contracts/public/map/reports.json";
import reportsTornado from "@contracts/public/map/reports.tornado.json";
import reportOptions from "@contracts/public/map/reports-options.json";
import { describe, expect, it } from "vitest";
import { ContractError } from "./guards";
import { parseReportOptions, parseStormReports } from "./reports";

function edit(value: unknown, change: (copy: Record<string, unknown>) => void): unknown {
  const copy = structuredClone(value) as Record<string, unknown>;
  change(copy);
  return copy;
}

/** The first report record of a copied fixture, for mutation in a test case. */
function firstReport(copy: Record<string, unknown>): Record<string, unknown> {
  const list = copy.reports as Record<string, unknown>[];
  return list[0] as Record<string, unknown>;
}

describe("storm report contracts", () => {
  it("parses the exported fixtures unchanged", () => {
    expect(parseStormReports(reports)).toEqual(reports);
    expect(parseStormReports(reportsTornado)).toEqual(reportsTornado);
    expect(parseReportOptions(reportOptions)).toEqual(reportOptions);
    // The provider's own filter produced each file's records.
    expect(parseStormReports(reportsTornado).reports).toHaveLength(1);
    expect(parseStormReports(reportsTornado).shownCount).toBe(1);
    expect(parseStormReports(reportsTornado).totalCount).toBe(4);
    // The significant hail is the diamond at 3.5 px.
    const significant = parseStormReports(reports).reports.filter((report) => report.significant);
    expect(significant).toHaveLength(1);
    expect(significant[0]?.symbol).toBe("diamond");
    expect(significant[0]?.radiusPx).toBe(3.5);
  });

  it.each([
    [
      "a shownCount that disagrees with the records",
      () =>
        parseStormReports(
          edit(reports, (c) => {
            c.shownCount = 3;
          }),
        ),
    ],
    [
      "a report colour that is not #rrggbb",
      () =>
        parseStormReports(
          edit(reports, (c) => {
            firstReport(c).stroke = "red";
          }),
        ),
    ],
    [
      "a report radius past the marker spec",
      () =>
        parseStormReports(
          edit(reports, (c) => {
            firstReport(c).radiusPx = 50;
          }),
        ),
    ],
    [
      "a report latitude off the globe",
      () =>
        parseStormReports(
          edit(reports, (c) => {
            firstReport(c).latitude = 95;
          }),
        ),
    ],
    [
      "an extra report key",
      () =>
        parseStormReports(
          edit(reports, (c) => {
            firstReport(c).kind = "report";
          }),
        ),
    ],
    [
      "an empty option list",
      () =>
        parseReportOptions(
          edit(reportOptions, (c) => {
            c.windows = [];
          }),
        ),
    ],
  ])("rejects %s", (_name, parse) => {
    expect(parse).toThrow(ContractError);
  });
});

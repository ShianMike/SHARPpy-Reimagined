import { describe, expect, it } from "vitest";
import modelCatalog from "@contracts/public/soundings/models.catalog.json";
import { parseModelCatalog } from "../../lib/contracts/soundings";
import { addHours, forecastLabel, formatUtc, latestRelease, releaseTime, resolveModelRun } from "./soundingTime";

describe("soundingTime", () => {
  it("preserves historical model cycles while rejecting invalid and future runs", () => {
    const model = parseModelCatalog(modelCatalog).models.find((item) => item.key === "gfs");
    if (model === undefined) throw new Error("Missing model fixture");
    expect(resolveModelRun(model, "2024-02-29T12:00:00Z")).toBe("2024-02-29T12:00:00Z");
    for (const run of [null, "", "2025-02-29T12:00:00Z", "2024-02-29T13:00:00Z", "2024-02-29T12:30:00Z", "9999-01-01T00:00:00Z", "0000-01-01T00:00:00Z"]) {
      expect(resolveModelRun(model, run)).toBe(model.suggestedRun);
    }
  });
  it("defaults to the newest 00Z or 12Z launch that has reached the archives", () => {
    expect(latestRelease(new Date("2026-10-04T13:59:00Z"))).toEqual({ date: "2026-10-04", hour: 0 });
    expect(latestRelease(new Date("2026-10-04T14:00:00Z"))).toEqual({ date: "2026-10-04", hour: 12 });
    expect(latestRelease(new Date("2026-10-04T01:00:00Z"))).toEqual({ date: "2026-10-03", hour: 12 });
  });

  it("builds RFC 3339 times and rejects malformed input", () => {
    expect(releaseTime("2026-10-04", 12)).toBe("2026-10-04T12:00:00Z");
    expect(releaseTime("2026-1-4", 12)).toBeNull();
    expect(releaseTime("2026-10-04", 24)).toBeNull();
    expect(releaseTime("2026-13-40", 0)).toBeNull();
    expect(releaseTime("2026-02-30", 0)).toBeNull();
    expect(releaseTime("2024-02-29", 23)).toBe("2024-02-29T23:00:00Z");
  });

  it("prints UTC times and forecast hours compactly", () => {
    expect(formatUtc("2026-06-26T00:00:00Z")).toBe("2026-06-26 00Z");
    expect(formatUtc("2026-06-26T00:30:00Z")).toBe("2026-06-26 00:30Z");
    expect(formatUtc(null)).toBe("\u2014");
    expect(addHours("2026-06-25T06:00:00Z", 18)).toBe("2026-06-26T00:00:00Z");
    expect(forecastLabel(7)).toBe("F007");
  });
});

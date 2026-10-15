import surface from "@contracts/public/map/surface.json";
import { describe, expect, it } from "vitest";
import { ContractError } from "./guards";
import { parseSurfacePlots } from "./surface";

function edit(value: unknown, change: (copy: Record<string, unknown>) => void): unknown {
  const copy = structuredClone(value) as Record<string, unknown>;
  change(copy);
  return copy;
}

describe("surface plot contracts", () => {
  it("parses the exported fixture unchanged", () => {
    const plots = parseSurfacePlots(surface);
    expect(plots.validTime).toBe("2026-10-04T21:00:00Z");
    expect(plots.plots).toHaveLength(6);
    expect(plots.counts.matched).toBe(4);
    expect(plots.counts.stale).toBe(1);
    expect(plots.counts.noObservation).toBe(1);
    expect(plots.counts.failed).toBe(1);
    expect(plots.counts.unqueried).toBe(0);
    // The 20 kt staff from the west: two barbs, no pennants.
    const koun = plots.plots[0] as (typeof plots.plots)[number];
    expect(koun.id).toBe("KOUN");
    expect(koun.staff).toEqual({ angleDeg: 180, pennants: 0, barbs: 2, halves: 0 });
    expect(koun.upperText).toBe("22");
    expect(koun.lowerText).toBe("12");
    const stale = plots.plots.find((plot) => plot.stale);
    expect(stale?.id).toBe("KSWA");
    expect(stale?.offsetMin).toBe(-45);
  });

  it.each([
    ["a staff out of the blue", (c: Record<string, unknown>) => {
      (((c.plots as unknown[])[0] as Record<string, unknown>).staff as Record<string, number>).barbs = 9;
    }],
    ["a missing staff on an available wind", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).staff = null;
    }],
    ["a staff on a calm wind", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[1] as Record<string, unknown>).staff = { angleDeg: 0, pennants: 0, barbs: 1, halves: 0 };
    }],
    ["a time without its offset", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).offsetMin = null;
    }],
    ["an offset out of the match window", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).offsetMin = -90;
    }],
    ["counts that disagree with the records", (c: Record<string, unknown>) => {
      (c.counts as Record<string, number>).stale = 2;
    }],
    ["coverage that does not add up", (c: Record<string, unknown>) => {
      (c.counts as Record<string, number>).nearby = 9;
    }],
    ["an unknown state", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).availability = "half";
    }],
    ["an unbounded identifier", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).id = "KOUN-KOUN-KOUN-KOU";
    }],
    ["an unbounded hover block", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).description = "x".repeat(601);
    }],
    ["an unbounded reading slot", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).upperText = "22222";
    }],
    ["a reading without its pair", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).lowerText = "";
    }],
    ["an angle the wrong side of north", (c: Record<string, unknown>) => {
      (((c.plots as unknown[])[0] as Record<string, unknown>).staff as Record<string, number>).angleDeg = 360;
    }],
    ["a second identifier", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).id = "KOKC";
    }],
    ["an extra key", (c: Record<string, unknown>) => {
      ((c.plots as unknown[])[0] as Record<string, unknown>).gust = 28;
    }],
  ])("rejects %s", (_name, change) => {
    expect(() => parseSurfacePlots(edit(surface, change))).toThrow(ContractError);
  });

  it("rejects the empty and the non-object", () => {
    expect(() => parseSurfacePlots(null)).toThrow(ContractError);
    expect(() => parseSurfacePlots("")).toThrow(ContractError);
  });
});

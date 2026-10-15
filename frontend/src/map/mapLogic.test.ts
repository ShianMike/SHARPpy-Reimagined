import mapCatalogFixture from "@contracts/public/map/catalog.json";
import modelCatalogFixture from "@contracts/public/soundings/models.catalog.json";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { resolveForecast } from "../features/forecast/useForecast";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import type { FieldFrame, SpcOutlook } from "../types/map";
import { coveringTime, trimFieldCache } from "./mapQueries";
import { defaultProductFor, productOf } from "./panelField";
import { hourAfter } from "./usePlayback";

const models = parseModelCatalog(modelCatalogFixture).models;
const catalog = parseMapCatalog(mapCatalogFixture);
const none = { modelKey: null, runTime: null, forecastHour: null };

describe("the forecast context", () => {
  it("starts on the field model's suggested run, its hours cut at the cycle's last", () => {
    const context = resolveForecast(models, catalog, none);
    expect(context?.model.key).toBe("hrrr");
    expect(context?.run).toBe("2026-10-04T16:00:00Z");
    expect(context?.fieldsAvailable).toBe(true);
    // A 16Z HRRR run stops at F018 (catalog maxForecastHours[16]).
    expect(context?.hours.at(-1)).toBe(18);
    expect(context?.validTime).toBe("2026-10-04T16:00:00Z");
  });

  it("offers 48 hours on a synoptic run, and clamps a later hour to the run's last", () => {
    const run = "2026-10-04T12:00:00Z";
    expect(resolveForecast(models, catalog, { ...none, runTime: run })?.hours.at(-1)).toBe(48);
    const clamped = resolveForecast(models, catalog, { ...none, runTime: "2026-10-04T16:00:00Z", forecastHour: 30 });
    expect(clamped?.hour).toBe(18);
    expect(clamped?.validTime).toBe("2026-10-05T10:00:00Z");
  });

  it("keeps another model's own hours, without map fields, and ignores unknown choices", () => {
    const gfs = resolveForecast(models, catalog, { ...none, modelKey: "gfs" });
    expect(gfs?.fieldsAvailable).toBe(false);
    expect(gfs?.hours.at(-1)).toBe(384);
    expect(resolveForecast(models, catalog, { modelKey: "nope", runTime: "1999-01-01T00:00:00Z", forecastHour: 7 })).toMatchObject({
      model: { key: "hrrr" },
      run: "2026-10-04T16:00:00Z",
      hour: 7,
    });
    expect(resolveForecast(models, undefined, none)?.fieldsAvailable).toBe(false);
    expect(resolveForecast([], catalog, none)).toBeNull();
  });

  it("steps through the hours, looping only when asked", () => {
    const hours = [0, 1, 2, 3];
    expect(hourAfter(hours, 1, 1, false)).toBe(2);
    expect(hourAfter(hours, 3, 1, false)).toBeNull();
    expect(hourAfter(hours, 3, 1, true)).toBe(0);
    expect(hourAfter(hours, 0, -1, true)).toBe(3);
    expect(hourAfter(hours, 9, 1, true)).toBe(0);
  });
});

describe("panel products", () => {
  it("gives each panel the first product of a different category, and honours a listed choice", () => {
    const keys = [0, 1, 2, 3].map((panel) => defaultProductFor(catalog, panel)?.category);
    expect(keys[0]).toBe(catalog.products.find((p) => p.key === catalog.defaultProduct)?.category);
    expect(new Set(keys).size).toBe(4);
    expect(productOf(catalog, "tmp-2m", 2)?.key).toBe("tmp-2m");
    expect(productOf(catalog, "not-listed", 0)?.key).toBe(catalog.defaultProduct);
    expect(productOf(undefined, "tmp-2m", 0)).toBeNull();
  });
});

describe("map query helpers", () => {
  const outlook = (validFrom: string, validTo: string, covers = true): SpcOutlook => ({
    product: "cat",
    validTime: validFrom,
    outlook: { title: "", subtitle: "", validFrom, validTo, issued: null, covers, sourceUrl: "", attribution: "", areas: [] },
  });

  it("reuses a loaded outlook's query time while the valid time stays in its window", () => {
    const cached = [[["outlooks", "cat", "2026-10-04T16:00:00Z"], outlook("2026-10-04T13:00:00Z", "2026-10-05T12:00:00Z")]] as const;
    expect(coveringTime(cached, "2026-10-04T21:00:00Z")).toBe("2026-10-04T16:00:00Z");
    expect(coveringTime(cached, "2026-10-05T12:00:00Z")).toBe("2026-10-05T12:00:00Z");
    const fallback = [[["outlooks", "cat", "x"], outlook("2026-10-04T13:00:00Z", "2026-10-05T12:00:00Z", false)]] as const;
    expect(coveringTime(fallback, "2026-10-04T21:00:00Z")).toBe("2026-10-04T21:00:00Z");
  });

  it("budgets raw cells and both render modes, dropping the oldest unobserved field frames", () => {
    const client = new QueryClient();
    const frame = (cells: number) => ({ cells: new Uint16Array(cells) }) as unknown as FieldFrame;
    [1, 2, 3].forEach((hour) => {
      client.setQueryData(["fields", "refc", "run", hour], frame(500), { updatedAt: hour * 1000 });
    });
    client.setQueryData(["outlooks", "cat", "t"], outlook("a", "b"));
    trimFieldCache(client, 10000);
    expect(client.getQueryData(["fields", "refc", "run", 1])).toBeUndefined();
    expect(client.getQueryData(["fields", "refc", "run", 2])).toBeDefined();
    expect(client.getQueryData(["fields", "refc", "run", 3])).toBeDefined();
    expect(client.getQueryData(["outlooks", "cat", "t"])).toBeDefined();
    client.clear();
  });

  it("reserves the incoming frame without evicting a visible panel's frame", () => {
    const client = new QueryClient();
    const frame = { cells: new Uint16Array(500) } as unknown as FieldFrame;
    [1, 2, 3].forEach((hour) => {
      client.setQueryData(["fields", "refc", "run", hour], frame, { updatedAt: hour * 1000 });
    });
    const observer = new QueryObserver(client, { queryKey: ["fields", "refc", "run", 1], enabled: false });
    const unsubscribe = observer.subscribe(() => undefined);
    try {
      trimFieldCache(client, 10000, frame);
      expect(client.getQueryData(["fields", "refc", "run", 1])).toBe(frame);
      expect(client.getQueryData(["fields", "refc", "run", 2])).toBeUndefined();
      expect(client.getQueryData(["fields", "refc", "run", 3])).toBeUndefined();
    } finally { unsubscribe(); client.clear(); }
  });

  it("budgets compact winds with fields and protects contours used by another panel", () => {
    const client = new QueryClient();
    const lines = { wind: { data: new Uint16Array(500) }, segments: [] };
    const old = ["contours", "hgt-wind-300", "run", 1], active = ["contours", "hgt-wind-300", "run", 2];
    client.setQueryData(old, lines, { updatedAt: 1000 });
    client.setQueryData(active, lines, { updatedAt: 2000 });
    const observer = new QueryObserver(client, { queryKey: active, enabled: false });
    const unsubscribe = observer.subscribe(() => undefined);
    try {
      trimFieldCache(client, 6000, { cells: new Uint16Array(500) } as unknown as FieldFrame);
      expect(client.getQueryData(old)).toBeUndefined();
      expect(client.getQueryData(active)).toBeDefined();
    } finally { unsubscribe(); client.clear(); }
  });
});

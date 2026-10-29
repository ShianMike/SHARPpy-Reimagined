import availability from "@contracts/public/map/availability.json";
import catalog from "@contracts/public/map/catalog.json";
import fieldShrf from "@contracts/public/map/field.shrf?url&inline";
import outlookCat from "@contracts/public/map/outlook.cat.json";
import outlookTorn from "@contracts/public/map/outlook.torn.json";
import outlookWind from "@contracts/public/map/outlook.wind.json";
import places from "@contracts/public/map/places.json";
import radarShrf from "@contracts/public/map/radar.shrf?url&inline";
import radarSiteShrf from "@contracts/public/map/radar-site.shrf?url&inline";
import radarSites from "@contracts/public/map/radar-sites.json";
import satelliteShrf from "@contracts/public/map/satellite.shrf?url&inline";
import { describe, expect, it } from "vitest";
import { encodeShrf, parseFieldFrame, parseRadarFrame, parseRadarSiteFrame, parseSatelliteFrame, parseShrf } from "./frames";
import { ContractError } from "./guards";
import { parseAvailabilityReport, parseMapCatalog, parsePlaceLabels, parseRadarSiteCatalog, parseSpcOutlook } from "./map";
import { parseCachedHours } from "./storage";

/** The bytes of a `?url&inline` data URL. */
function bytes(dataUrl: string): Uint8Array {
  return Uint8Array.from(atob(dataUrl.split(",")[1] ?? ""), (c) => c.charCodeAt(0));
}

function edit(value: unknown, change: (copy: Record<string, unknown>) => void): unknown {
  const copy = structuredClone(value) as Record<string, unknown>;
  change(copy);
  return copy;
}

describe("map contracts", () => {
  it("accepts global cache hours through F384 and rejects invalid or unordered hours", () => {
    expect(parseCachedHours([0, 144, 360, 384])).toEqual([0, 144, 360, 384]);
    const gfs = [...Array.from({ length: 121 }, (_, hour) => hour), ...Array.from({ length: 88 }, (_, index) => 123 + index * 3)];
    expect(parseCachedHours(gfs)).toHaveLength(209);
    for (const invalid of [[385], [-1], [NaN], [1.5], [6, 0], [0, 0]]) {
      expect(() => parseCachedHours(invalid)).toThrow(ContractError);
    }
  });

  it("parses the exported JSON fixtures and defaults omitted product start hours", () => {
    expect(parseMapCatalog(catalog)).toEqual({ ...catalog, products: catalog.products.map((product) => ({ ...product, minimumForecastHour: product.minimumForecastHour ?? 0 })) });
    for (const outlook of [outlookCat, outlookTorn, outlookWind]) {
      expect(parseSpcOutlook(outlook)).toEqual(outlook);
    }
    expect(parseSpcOutlook(outlookWind).outlook).toBeNull();
    expect(parsePlaceLabels(places)).toEqual(places);
    expect(parseAvailabilityReport(availability)).toEqual(availability);
  });

  it("accepts optional product start hours only as integers from F000 to F384", () => {
    const legacy = structuredClone(catalog);
    for (const product of legacy.products) Reflect.deleteProperty(product, "minimumForecastHour");
    expect(parseMapCatalog(legacy).products.every((product) => product.minimumForecastHour === 0)).toBe(true);
    const product = legacy.products[0];
    if (product === undefined) throw new Error("Missing field product fixture");
    for (const hour of [0, 1, 3, 384]) {
      Reflect.set(product, "minimumForecastHour", hour);
      expect(parseMapCatalog(legacy).products[0]?.minimumForecastHour).toBe(hour);
    }
    for (const hour of [-1, 385, 1.5, "3", null, NaN]) {
      Reflect.set(product, "minimumForecastHour", hour);
      expect(() => parseMapCatalog(legacy)).toThrow("products[0].minimumForecastHour");
    }
  });

  it("reads the field frame: header, cells, and the value rule", () => {
    const frame = parseFieldFrame(bytes(fieldShrf).buffer);
    expect(frame).toMatchObject({ product: "refc", forecastHour: 1, width: 256, height: 128, nodata: 65535 });
    expect(frame.cells).toHaveLength(256 * 128);
    const values = Array.from(frame.cells.filter((cell) => cell !== frame.nodata), (c) => c * frame.scale + frame.offset);
    expect(Math.min(...values)).toBeCloseTo(frame.valueMin, 6);
    expect(Math.max(...values)).toBeCloseTo(frame.valueMax, 3);
  });

  it("reads the radar frame's header and PNG", async () => {
    const frame = parseRadarFrame(bytes(radarShrf).buffer);
    expect(frame.bounds).toEqual({ west: -130, east: -60, south: 20, north: 55 });
    expect(frame.png.type).toBe("image/png");
    expect(new Uint8Array(await frame.png.arrayBuffer()).slice(1, 4)).toEqual(Uint8Array.from([0x50, 0x4e, 0x47]));
  });

  it("accepts only bounded PNG legends and validated numeric palettes", () => {
    const frame = parseShrf(bytes(radarShrf));
    const encoded = btoa(String.fromCharCode(...frame.body.slice(0, 8)));
    expect(parseRadarFrame(encodeShrf({ ...frame.header, legendPngBase64: encoded }, frame.body)).legendPng).toBe(`data:image/png;base64,${encoded}`);
    for (const legendPngBase64 of ["not base64!", btoa("<svg/>"), "A".repeat(175001)]) {
      expect(() => parseRadarFrame(encodeShrf({ ...frame.header, legendPngBase64 }, frame.body))).toThrow(ContractError);
    }
    const site = parseShrf(bytes(radarSiteShrf));
    const palette = { stops: [[-200, "#ffdadc"], [200, "#2d0000"]], units: "mph", display: "fixed", stepped: false, floor: null, ceiling: null, ticks: [-200, 0, 200] };
    expect(parseRadarSiteFrame(encodeShrf({ ...site.header, palette }, site.body)).palette).toEqual(palette);
    expect(() => parseRadarSiteFrame(encodeShrf({ ...site.header, palette: { ...palette, stops: [] } }, site.body))).toThrow(ContractError);
  });

  it("reads the single-site frame: the mosaic's shape plus the serving site", () => {
    const frame = parseRadarSiteFrame(bytes(radarSiteShrf).buffer);
    expect(frame).toMatchObject({ product: "reflectivity", siteId: "KTLX", label: "Base Reflectivity", units: "dBZ" });
    expect(frame.bounds.west).toBeCloseTo(-102.278, 3);
    expect(frame.png.type).toBe("image/png");
    expect(parseRadarSiteCatalog(radarSites).sites.length).toBeGreaterThan(100);
    const { header, body } = parseShrf(bytes(radarSiteShrf));
    expect(parseRadarSiteFrame(encodeShrf({ ...header, elevationAngle: 1.3 }, body)).elevationAngle).toBe(1.3);
    for (const elevationAngle of [-2, 91, "1.3"]) {
      expect(() => parseRadarSiteFrame(encodeShrf({ ...header, elevationAngle }, body))).toThrow(ContractError);
    }
  });

  it("preserves optional radar names and capabilities without changing legacy US sites", () => {
    const legacy = { id: "KTLX", latitude: 35.3333, longitude: -97.2783 };
    const australian = { id: "ABRD", latitude: -27.717, longitude: 153.24, name: "Broadmeadows", country: "AU", products: ["reflectivity"] };
    const parsed = parseRadarSiteCatalog({ sites: [legacy, australian] });
    expect(parsed.sites).toEqual([legacy, australian]);
    australian.products.push("velocity");
    expect(parsed.sites[1]?.products).toEqual(["reflectivity"]);
    for (const metadata of [{ name: 42 }, { country: "Australia" }, { products: "reflectivity" }, { products: [null] }]) {
      expect(() => parseRadarSiteCatalog({ sites: [{ ...legacy, ...metadata }] })).toThrow(ContractError);
    }
  });

  it("reads the satellite frame and its scan provenance", () => {
    const frame = parseSatelliteFrame(bytes(satelliteShrf).buffer);
    expect(frame).toMatchObject({
      product: "infrared",
      satellite: "G19",
      requestedTime: "2026-10-04T21:00:00Z",
      validTime: "2026-10-04T21:00:00Z",
    });
    expect(frame.coverageFraction).toBeCloseTo(0.87, 6);
    expect(frame.nativeResolutionKm).toBeCloseTo(2, 6);
    expect(frame.png.type).toBe("image/png");
  });

  it("round-trips a frame and starts the body on an 8-byte boundary", () => {
    for (const header of [{ a: 1 }, { abc: "defgh" }, { kind: "x", n: [1, 2, 3] }]) {
      const body = Uint8Array.from([1, 2, 3, 4]);
      const encoded = encodeShrf(header, body);
      const parsed = parseShrf(encoded);
      expect(parsed.header).toEqual(header);
      expect(Array.from(parsed.body)).toEqual([1, 2, 3, 4]);
      expect(parsed.body.byteOffset % 8).toBe(0);
    }
  });

  it("keeps ordinary offset input as a view and snapshots shared input", () => {
    const encoded = bytes(fieldShrf);
    const padded = new Uint8Array(encoded.length + 16);
    padded.set(encoded, 8);
    const input = padded.subarray(8, 8 + encoded.length);
    const parsed = parseShrf(input);
    expect(parsed.body.buffer).toBe(padded.buffer);
    expect(parseFieldFrame(input).cells.buffer).toBe(padded.buffer);
    const shared = new Uint8Array(new SharedArrayBuffer(encoded.length + 16), 8, encoded.length);
    shared.set(encoded);
    const snapshot = parseShrf(shared);
    shared.fill(0);
    expect(snapshot.body.buffer).toBeInstanceOf(ArrayBuffer);
    expect(snapshot.body).toEqual(parseShrf(encoded).body);
  });

  it.each([
    ["radar", radarShrf, parseRadarFrame],
    ["site radar", radarSiteShrf, parseRadarSiteFrame],
    ["satellite", satelliteShrf, parseSatelliteFrame],
  ] as const)("keeps %s PNG bytes independent of mutable input and rejects invalid bodies", async (_name, fixture, parse) => {
    const encoded = bytes(fixture);
    const { header, body } = parseShrf(encoded);
    const expected = body.slice();
    for (const kind of ["buffer", "offset", "array", "shared"] as const) {
      const input = kind === "shared" ? new Uint8Array(new SharedArrayBuffer(encoded.length + 16)) : new Uint8Array(encoded.length + 16);
      input.set(encoded, 8);
      const view = input.subarray(8, 8 + encoded.length);
      const value = kind === "buffer" ? encoded.slice().buffer : kind === "array" ? Array.from(view) : view;
      const frame = parse(value);
      if (value instanceof ArrayBuffer) new Uint8Array(value).fill(0);
      else value.fill(0);
      expect(frame.png.type).toBe("image/png");
      expect(new Uint8Array(await frame.png.arrayBuffer())).toEqual(expected);
    }
    for (const invalid of [body.subarray(0, 7), new Uint8Array(body.length)]) {
      expect(() => parse(encodeShrf(header, invalid))).toThrow(ContractError);
    }
  });

  it("rejects malformed frames", () => {
    const field = parseShrf(bytes(fieldShrf).buffer);
    const short = encodeShrf(field.header, field.body.subarray(2));
    const odd = encodeShrf({ ...field.header, scale: 0 }, field.body);
    const extra = encodeShrf({ ...field.header, extra: 1 }, field.body);
    const notRadar = encodeShrf({ ...parseShrf(bytes(radarShrf).buffer).header }, Uint8Array.from([1, 2, 3]));
    const truncated = bytes(fieldShrf).slice(0, 40);
    for (const run of [
      () => parseShrf(Uint8Array.from([0x53, 0x48, 0x52, 0x47, 1, 0, 0, 0, 0, 0, 0, 0])),
      () => parseShrf(truncated),
      () => parseShrf("SHRF"),
      () => parseFieldFrame(short),
      () => parseFieldFrame(odd),
      () => parseFieldFrame(extra),
      () => parseRadarFrame(notRadar),
      () => parseRadarFrame(bytes(fieldShrf).buffer),
    ]) {
      expect(run).toThrow(ContractError);
    }
  });

  const stops = (c: Record<string, unknown>): unknown[][] =>
    (c.products as { palette: { stops: unknown[][] } }[])[0]?.palette.stops ?? [];
  const firstRing = (c: Record<string, unknown>): unknown[] =>
    (c.outlook as { areas: { rings: unknown[][] }[] }).areas[0]?.rings[0] ?? [];
  it.each([
    ["a bad colour", () => parseMapCatalog(edit(catalog, (c) => stops(c)[0]?.splice(1, 1, "red")))],
    ["an unknown display quantity", () => parseMapCatalog(edit(catalog, (c) => { ((c.products as { palette: Record<string, unknown> }[])[0] as { palette: Record<string, unknown> }).palette.display = "unknown"; }))],
    ["stops out of order", () => parseMapCatalog(edit(catalog, (c) => stops(c).reverse()))],
    ["too many colour stops", () => parseMapCatalog(edit(catalog, (c) => stops(c).splice(0, stops(c).length, ...Array.from({ length: 257 }, (_, i) => [i, "#123456"]))))],
    ["an unlisted default product", () => parseMapCatalog(edit(catalog, (c) => (c.defaultProduct = "none")))],
    ["23 cycle limits", () => parseMapCatalog(edit(catalog, (c) => (c.maxForecastHours as unknown[]).pop()))],
    ["a vertex off the globe", () => parseSpcOutlook(edit(outlookCat, (c) => firstRing(c).splice(0, 1, [200, 10])))],
    ["a tier 3 place", () => parsePlaceLabels(edit(places, (c) => (c.places as Record<string, unknown>[]).splice(0, 1, { name: "x", latitude: 1, longitude: 1, tier: 3 })))],
    ["an extra station key", () => parseAvailabilityReport(edit(availability, (c) => (c.extraStations as unknown[]).push({ id: "1" })))],
  ])("rejects %s", (_name, parse) => {
    expect(parse).toThrow(ContractError);
  });

  it.each([
    ["a site frame whose header lost its siteId", () => {
      const { header, body } = parseShrf(bytes(radarSiteShrf).buffer);
      const stripped = structuredClone(header) as Record<string, unknown>;
      delete stripped.siteId;
      return parseRadarSiteFrame(encodeShrf(stripped, body));
    }],
    ["a site catalogue entry off the globe", () =>
      parseRadarSiteCatalog(edit(radarSites, (c) => ((c.sites as Record<string, unknown>[])[0] as { latitude: number }).latitude = 95))],
    ["a satellite frame with no scan time", () => {
      const { header, body } = parseShrf(bytes(satelliteShrf).buffer);
      const stripped = structuredClone(header) as Record<string, unknown>;
      delete stripped.validTime;
      return parseSatelliteFrame(encodeShrf(stripped, body));
    }],
  ])("rejects %s", (_name, parse) => {
    expect(parse).toThrow(ContractError);
  });
});

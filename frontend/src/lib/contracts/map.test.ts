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
  it("parses the exported JSON fixtures without changing them", () => {
    expect(parseMapCatalog(catalog)).toEqual(catalog);
    for (const outlook of [outlookCat, outlookTorn, outlookWind]) {
      expect(parseSpcOutlook(outlook)).toEqual(outlook);
    }
    expect(parseSpcOutlook(outlookWind).outlook).toBeNull();
    expect(parsePlaceLabels(places)).toEqual(places);
    expect(parseAvailabilityReport(availability)).toEqual(availability);
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

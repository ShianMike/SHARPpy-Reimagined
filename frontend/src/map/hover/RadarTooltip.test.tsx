// @vitest-environment jsdom
import { Blob as NodeBlob } from "node:buffer";
import { crc32, deflateSync } from "node:zlib";
import radar from "@contracts/public/map/radar.shrf?url&inline";
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { encodeShrf, parseShrf } from "../../lib/contracts/frames";
import { renderWithBackend } from "../../test/backendHarness";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import { useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { RadarScale } from "../../features/tracking/RadarScale";
import { decodeRadarValues, radarCodeAt } from "../radarValues";
import { useHoverStore } from "./hoverStore";
import { RadarTooltip } from "./RadarTooltip";

const codes = [[0, 1, 2, 136], [2, 6, 4, 8], [130, 2, 6, 240], [255, 128, 7, 33], [44, 5, 1, 0]];
const bounds = { west: -100, east: -96, south: 30, north: 35 };
const initial = useMapStore.getState(), app = useAppStore.getState();
const clock = useLiveTrackingStore.getState(), now = Date.parse("2026-10-17T08:00:00Z");
const hover = { panel: 0, longitude: -96.5, latitude: 34.5, x: 100, y: 150 };

function chunk(type: string, data: Uint8Array): Buffer {
  const payload = Buffer.concat([Buffer.from(type), data]);
  const size = Buffer.alloc(4), check = Buffer.alloc(4);
  size.writeUInt32BE(data.length); check.writeUInt32BE(crc32(payload));
  return Buffer.concat([size, payload, check]);
}

function scan(metadata: unknown = { values: Array.from({ length: 256 }, (_, code) => code < 2 ? null : code / 2 - 33), folded: 1, lowerBounds: [240] }, extra = 0): Uint8Array<ArrayBuffer> {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(4); header.writeUInt32BE(5, 4); header[8] = 8; header[9] = 3;
  const rows: number[] = [];
  codes.forEach((row, filter) => {
    rows.push(filter);
    row.forEach((value, col) => {
      const left = row[col - 1] ?? 0, up = codes[filter - 1]?.[col] ?? 0, corner = codes[filter - 1]?.[col - 1] ?? 0;
      const p = left + up - corner, a = Math.abs(p - left), b = Math.abs(p - up), c = Math.abs(p - corner);
      const predictor = filter === 0 ? 0 : filter === 1 ? left : filter === 2 ? up : filter === 3 ? Math.floor((left + up) / 2) : a <= b && a <= c ? left : b <= c ? up : corner;
      rows.push((value - predictor + 256) % 256);
    });
  });
  const pixels = deflateSync(Uint8Array.from([...rows, ...new Array<number>(extra).fill(0)]));
  return Uint8Array.from(Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header),
    ...(metadata === null ? [] : [chunk("tEXt", Buffer.from(`sharppy-radar\0${JSON.stringify(metadata)}`))]),
    chunk("PLTE", Buffer.alloc(768)), chunk("IDAT", pixels), chunk("IEND", new Uint8Array())]));
}

beforeEach(() => {
  vi.spyOn(Date, "now").mockReturnValue(now);
  vi.stubGlobal("Blob", NodeBlob);
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 4, height: 5 }));
  useMapStore.setState(initial, true); useAppStore.setState(app, true); useHoverStore.getState().clear();
  useLiveTrackingStore.setState({ ...clock, now, selectedTime: null }, true);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterEach(() => { cleanup(); useMapStore.setState(initial, true); useAppStore.setState(app, true); useLiveTrackingStore.setState(clock, true); useHoverStore.getState().clear(); vi.unstubAllGlobals(); });

it("decodes every PNG filter without losing native codes or values beyond the colour scale", async () => {
  const samples = await decodeRadarValues(new Blob([scan()]));
  expect(samples).not.toBeNull();
  if (samples === null) throw new Error("missing samples");
  codes.forEach((row, r) => { row.forEach((code, c) => {
    expect(radarCodeAt(samples, bounds, -99.5 + c, 34.5 - r)).toBe(code);
  }); });
  expect(samples.values[255]).toBe(94.5);
  expect(samples.values[0]).toBeNull(); expect(samples.folded).toBe(1); expect(samples.lowerBounds.has(240)).toBe(true);
  expect(radarCodeAt(samples, bounds, -96, 30)).toBe(0);
  expect(radarCodeAt(samples, bounds, -101, 32)).toBe(0);
  expect(radarCodeAt(samples, bounds, NaN, 32)).toBe(0);
});

it("ignores images without native values and rejects malformed calibration or oversized decompression", async () => {
  await expect(decodeRadarValues(new Blob([scan(null)]))).resolves.toBeNull();
  await expect(decodeRadarValues(new Blob([scan({ values: [1], folded: null, lowerBounds: [] })]))).rejects.toThrow("256");
  await expect(decodeRadarValues(new Blob([scan(undefined, 1)]))).rejects.toThrow("dimensions");
  const truncated = scan().slice(0, -15);
  await expect(decodeRadarValues(new Blob([truncated]))).rejects.toThrow();
});

it.each([
  ["reflectivity", "Base Reflectivity", "dBZ", 35, "35.0 dBZ", 12],
  ["velocity", "Velocity", "mph", -100, "-100 mph", 10],
  ["correlation-coefficient", "Correlation Coefficient", "", 0.987, "0.987", 12],
  ["echo-tops", "Enhanced Echo Tops", "kft", 40, "40.0 kft", 0],
])("shows %s from the hovered panel and scan without requesting on pointer moves", async (product, label, units, value, text, delay) => {
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setRadarScope("site"); useMapStore.getState().setLayerVisible("radar", true);
  useMapStore.getState().setRadarSite("KTLX"); useMapStore.getState().setRadarProduct(product);
  const values = Array.from({ length: 256 }, (_, code) => code < 2 ? null : code === 136 ? value : code === 240 ? 50 : code / 2 - 33);
  const png = scan({ values, folded: product === "echo-tops" || product === "correlation-coefficient" ? null : 1, lowerBounds: product === "echo-tops" ? [240] : [] });
  const source = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (char) => char.charCodeAt(0)).buffer);
  const transport = createTransport(), call = transport.call.bind(transport);
  const request = vi.fn<typeof transport.call>((command, args) => command === "get_radar_site_frame"
    ? Promise.resolve(encodeShrf({ ...source.header, kind: "radar-site", siteId: "KTLX", product, label, units, bounds, validTime: new Date(now - delay * 60_000).toISOString().replace(".000Z", "Z") }, png)) : call(command, args));
  transport.call = request;
  const { queryClient, view } = renderWithBackend(<><RadarTooltip panel={0} /><RadarScale panel={0} /></>, transport);
  act(() => { useHoverStore.getState().setPointer(hover); });
  await waitFor(() => { expect(screen.getByTestId("pointer-value").textContent).toContain(text); });
  expect(screen.getByTestId("pointer-value").textContent).toContain(`KTLX · ${label}`);
  expect(screen.getByTestId("radar-scale").getAttribute("aria-description")).toContain("Scan 2026-10-17");
  await waitFor(() => { expect(screen.queryByRole("img", { name: "Radar scan is 12 minutes behind the selected time" }) !== null).toBe(delay > 10); });
  act(() => { useHoverStore.getState().setOutlook({ label: "Tornado Warning", description: "", title: "NWS", subtitle: "", x: 100, y: 150 }); });
  expect(screen.getByTestId("pointer-value").textContent).toContain(text);
  act(() => { useHoverStore.getState().setOutlook(null); });
  const calls = request.mock.calls.length;
  act(() => { useHoverStore.getState().setPointer({ ...hover, longitude: -98.5, x: 120 }); });
  expect(screen.getByTestId("pointer-value").textContent).toContain(product === "echo-tops" || product === "correlation-coefficient" ? "No data here" : "Range folded");
  act(() => { useHoverStore.getState().setPointer({ ...hover, longitude: -99.5 }); });
  expect(screen.getByTestId("pointer-value").textContent).toContain("No data here");
  if (product === "echo-tops") {
    act(() => { useHoverStore.getState().setPointer({ ...hover, latitude: 32.5 }); });
    expect(screen.getByTestId("pointer-value").textContent).toContain("≥ 50.0 kft");
  }
  expect(request).toHaveBeenCalledTimes(calls);
  act(() => { useHoverStore.getState().setPointer({ ...hover, panel: 1 }); });
  expect(screen.queryByTestId("pointer-value")).toBeNull();
  act(() => { useHoverStore.getState().setPointer(hover); useMapStore.getState().setLayerVisible("radar", false); });
  expect(screen.queryByTestId("pointer-value")).toBeNull();
  view.unmount(); queryClient.clear();
});

// @vitest-environment jsdom
import radarSite from "@contracts/public/map/radar-site.shrf?url&inline";
import radarTilts from "@contracts/public/map/radar-tilts.json";
import { QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { createQueryClient, mapKeys } from "../../lib/backend/queryOptions";
import { parseRadarSiteFrame } from "../../lib/contracts/frames";
import { parseRadarTilts } from "../../lib/contracts/map";
import { useAppStore } from "../../stores/appStore";
import { LIVE_MINUTE } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { isLiveQuery, preloadRadarTilts, trimLiveCache } from "./liveRequests";

const map = useMapStore.getState(), app = useAppStore.getState();
const now = Date.parse("2026-10-17T08:00:00Z");
beforeEach(() => { useMapStore.setState(map, true); useAppStore.setState(app, true); });
afterEach(() => { vi.restoreAllMocks(); });

it("bounds raster memory and unavailable cuts without removing active imagery or model data", async () => {
  const client = createQueryClient();
  const activeKey = mapKeys.radar(null, new Date(now).toISOString());
  client.setQueryData(activeKey, { png: new Blob(["active"]) }, { updatedAt: 1 });
  const observer = new QueryObserver(client, { queryKey: activeKey, enabled: false });
  const unsubscribe = observer.subscribe(() => undefined);
  for (let step = 1; step <= 20; step++) client.setQueryData(mapKeys.satellite("visible", new Date(now - step * LIVE_MINUTE).toISOString()), { png: new Blob(["scan"]) }, { updatedAt: step + 1 });
  const fieldKey = mapKeys.field("refc", new Date(now).toISOString(), 0);
  client.setQueryData(fieldKey, "forecast");
  trimLiveCache(client, 32);
  expect(client.getQueryData(activeKey)).toBeDefined();
  expect(client.getQueryData(fieldKey)).toBe("forecast");
  expect(client.getQueryCache().findAll({ predicate: (query) => isLiveQuery(query.queryKey) })).toHaveLength(9);
  await Promise.allSettled(Array.from({ length: 140 }, (_, step) => client.query({
    queryKey: mapKeys.radarSite("velocity", "KTLX", new Date(now - step * LIVE_MINUTE).toISOString(), "z"),
    queryFn: () => { throw new Error("No published cut"); }, retry: false,
  })));
  trimLiveCache(client);
  expect(client.getQueryCache().findAll({ predicate: (query) => isLiveQuery(query.queryKey) }).length).toBeLessThanOrEqual(129);
  expect(client.getQueryData(activeKey)).toBeDefined();
  expect(client.getQueryData(fieldKey)).toBe("forecast");
  unsubscribe(); client.clear();
});

it.each(["reflectivity", "correlation-coefficient"])("preloads shared %s cuts with two requests and no retry of unavailable cuts", async (product) => {
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setPanelLayout(2);
  for (const panel of [0, 1]) {
    useMapStore.getState().setActivePanel(panel);
    useMapStore.getState().setRadarScope("site");
    useMapStore.getState().setRadarSite("KTLX");
    useMapStore.getState().setRadarProduct(product);
  }
  const frame = parseRadarSiteFrame(Uint8Array.from(atob(radarSite.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
  const client = new BackendClient(createTransport());
  const queryClient = createQueryClient();
  let pending = 0, peak = 0;
  const fetch = vi.spyOn(client, "getRadarSiteFrame").mockImplementation(async (_where, product, validTime, tilt) => {
    peak = Math.max(peak, ++pending);
    await new Promise<void>((resolve) => { setTimeout(resolve, 0); });
    pending--;
    if (!["0", "1", "2", "3"].includes(tilt ?? "0")) throw new Error("No published cut");
    return { ...frame, product: product ?? "reflectivity", validTime: validTime ?? frame.validTime };
  });
  const time = "2026-10-17T08:00:00Z";
  await preloadRadarTilts(client, queryClient, useMapStore.getState(), null, time, () => false);
  expect(fetch.mock.calls[0]).toEqual([{ site: "KTLX" }, product, time, "0"]);
  expect(fetch).toHaveBeenCalledTimes(product === "correlation-coefficient" ? 12 : 8);
  expect(fetch.mock.calls.every((call) => ["0", "1", "2", "3"].includes(call[3] ?? "0"))).toBe(true);
  expect(fetch.mock.calls.some((call) => ["x", "y"].includes(call[3] ?? "0"))).toBe(false);
  expect(peak).toBe(2);
  expect(queryClient.getQueryData(mapKeys.radarSite("velocity", "KTLX", time, "3"))).toBeDefined();
  fetch.mockClear();
  await preloadRadarTilts(client, queryClient, useMapStore.getState(), null, time, () => true);
  expect(fetch).not.toHaveBeenCalled();
  queryClient.clear();
});

it("bounds inventory checks across four distinct sites and skips all unverified background images", async () => {
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setPanelLayout(4);
  for (const [panel, site] of ["KTLX", "PGUA", "PAHG", "TJUA"].entries()) {
    useMapStore.getState().setActivePanel(panel);
    useMapStore.getState().setRadarScope("site"); useMapStore.getState().setRadarSite(site); useMapStore.getState().setRadarProduct("velocity");
  }
  const client = new BackendClient(createTransport()), queryClient = createQueryClient();
  const images = vi.spyOn(client, "getRadarSiteFrame").mockRejectedValue(new Error("selected frame unavailable"));
  let pending = 0, peak = 0;
  const checks = vi.spyOn(client, "getRadarTilts").mockImplementation(async (site, product) => {
    peak = Math.max(peak, ++pending);
    await new Promise<void>((resolve) => { setTimeout(resolve, 0); });
    pending--;
    return parseRadarTilts({ ...radarTilts, siteId: site, product, tilts: radarTilts.tilts.map((cut) => ({ ...cut, status: "unknown" })) });
  });
  await preloadRadarTilts(client, queryClient, useMapStore.getState(), null, "2026-10-17T08:00:00Z", () => false);
  expect(checks).toHaveBeenCalledTimes(8); expect(peak).toBe(2);
  expect(images).toHaveBeenCalledTimes(4);
  queryClient.clear();
});

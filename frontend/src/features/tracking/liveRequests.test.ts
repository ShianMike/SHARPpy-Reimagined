// @vitest-environment jsdom
import radarSite from "@contracts/public/map/radar-site.shrf?url&inline";
import { QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { BackendFailure } from "../../lib/backend/errors";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { createQueryClient, mapKeys, RADAR_HISTORY_STALE_MS } from "../../lib/backend/queryOptions";
import { parseRadarSiteFrame } from "../../lib/contracts/frames";
import { useAppStore } from "../../stores/appStore";
import { LIVE_MINUTE } from "../../stores/liveTrackingStore";
import { useMapStore } from "../../stores/mapStore";
import { fetchLiveRequest, isLiveQuery, liveRequests, trimLiveCache } from "./liveRequests";

const map = useMapStore.getState(), app = useAppStore.getState();
const now = Date.parse("2026-10-17T08:00:00Z");
beforeEach(() => { useMapStore.setState(map, true); useAppStore.setState(app, true); });
afterEach(() => { vi.restoreAllMocks(); });

it("reuses a confirmed alert history gap on playback loops but retries other provider failures", async () => {
  const client = createQueryClient();
  const missing = new BackendFailure({ code: "OVERLAY_FETCH_FAILED", message: "No snapshot", retryable: false,
    details: { reason: "historyUnavailable" } });
  const queryFn = vi.fn().mockRejectedValue(missing);
  const request = { queryKey: mapKeys.alerts("2026-10-30T02:40:00Z"), queryFn };
  await expect(fetchLiveRequest(client, request)).rejects.toBe(missing);
  await expect(fetchLiveRequest(client, request)).rejects.toBe(missing);
  await expect(fetchLiveRequest(client, request, true)).rejects.toBe(missing);
  expect(queryFn).toHaveBeenCalledOnce();
  const outage = new BackendFailure({ code: "OVERLAY_FETCH_FAILED", message: "Network unavailable", retryable: true });
  const retry = vi.fn().mockRejectedValueOnce(outage).mockResolvedValue("current snapshot");
  const current = { queryKey: mapKeys.alerts("2026-10-30T02:47:00Z"), queryFn: retry };
  await expect(fetchLiveRequest(client, current)).rejects.toBe(outage);
  await expect(fetchLiveRequest(client, current, true)).resolves.toBe("current snapshot");
  expect(retry).toHaveBeenCalledTimes(2);
  client.clear();
});

it.each([
  { queryKey: mapKeys.radar(null, "2026-10-17T08:00:00Z") },
  { queryKey: mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T08:00:00Z") },
])("rechecks an expired radar frame during playback but reuses a fresh one", async ({ queryKey }) => {
    const client = createQueryClient();
    const queryFn = vi.fn().mockResolvedValue("new scan");
    client.setQueryData(queryKey, "old scan", { updatedAt: Date.now() - RADAR_HISTORY_STALE_MS - 1 });
    await expect(fetchLiveRequest(client, { queryKey, queryFn })).resolves.toBe("new scan");
    await expect(fetchLiveRequest(client, { queryKey, queryFn })).resolves.toBe("new scan");
    expect(queryFn).toHaveBeenCalledOnce();
    client.clear();
});

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

it.each([
  ["pinned", ["reflectivity"], "reflectivity"],
  ["nearest", ["reflectivity"], "reflectivity"],
  ["pinned", ["reflectivity", "velocity"], "velocity"],
  ["nearest", ["reflectivity", "velocity"], "velocity"],
] as const)("uses %s Australian history with supported %s products", async (mode, products, product) => {
  useAppStore.getState().setActiveWorkspace("live");
  const state = useMapStore.getState();
  state.setPanelLayout(2);
  for (const [panel, site] of [mode === "pinned" ? "ABRD" : null, "KTLX"].entries()) {
    state.setActivePanel(panel); state.setRadarScope("site"); state.setRadarSite(site ?? null);
    state.setRadarProduct("velocity"); state.setRadarTilt("2");
  }
  const sites = [{ id: "ABRD", name: "Broadmeadows", country: "AU", latitude: -37.85, longitude: 144.75, products }];
  const client = new BackendClient(createTransport());
  const frame = parseRadarSiteFrame(Uint8Array.from(atob(radarSite.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
  const fetch = vi.spyOn(client, "getRadarSiteFrame").mockResolvedValue(frame);
  const time = "2026-10-17T08:00:00Z";
  const requests = liveRequests(client, useMapStore.getState(), "ABRD", null, time, sites);
  expect(requests.map(({ queryKey }) => queryKey)).toEqual([
    mapKeys.radarSite(product, "ABRD", time, "0"), mapKeys.radarSite("velocity", "KTLX", time, "2"),
  ]);
  await requests[0]?.queryFn();
  expect(fetch).toHaveBeenCalledWith({ site: "ABRD" }, product, time, "0");
});

it("uses provider metadata for MRRL playback without requesting NEXRAD tilts", () => {
  useAppStore.getState().setActiveWorkspace("live");
  const state = useMapStore.getState();
  state.setRadarScope("site"); state.setRadarSite("TEST"); state.setRadarProduct("velocity"); state.setRadarTilt("2");
  const sites = [{ id: "TEST", country: "NC", provider: "MRRL", products: ["reflectivity"], latitude: -22.27, longitude: 166.45 }];
  const client = new BackendClient(createTransport());
  const frames = vi.spyOn(client, "getRadarSiteFrame"), tilts = vi.spyOn(client, "getRadarTilts");
  const time = "2026-10-17T08:00:00Z";
  expect(liveRequests(client, useMapStore.getState(), null, null, time, sites).map(({ queryKey }) => queryKey))
    .toContainEqual(mapKeys.radarSite("reflectivity", "TEST", time, "0"));
  expect(frames).not.toHaveBeenCalled();
  expect(tilts).not.toHaveBeenCalled();
});

it("keeps Australian velocity while the site catalogue is loading", () => {
  useAppStore.getState().setActiveWorkspace("live");
  const state = useMapStore.getState();
  state.setRadarScope("site"); state.setRadarSite("ABRD"); state.setRadarProduct("velocity"); state.setRadarTilt("2");
  const time = "2026-10-17T08:00:00Z";
  const client = new BackendClient(createTransport());
  expect(liveRequests(client, useMapStore.getState(), null, null, time).map(({ queryKey }) => queryKey))
    .toContainEqual(mapKeys.radarSite("velocity", "ABRD", time, "0"));
});

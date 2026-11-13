// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, type QueryKey } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import { createQueryClient, mapKeys } from "../lib/backend/queryOptions";
import { RADAR_TILTS, type RadarSite, type RadarTilts } from "../types/map";
import { useUnavailableRadarSites } from "./radarSiteAvailability";

const sites: RadarSite[] = [
  { id: "EMPTY", longitude: 0, latitude: 0, products: [] },
  { id: "KTLX", longitude: 1, latitude: 1 },
];
const firstTime = "2026-10-17T07:55:00Z", secondTime = "2026-10-17T08:00:00Z";

function attempt(client: QueryClient, key: QueryKey, status: "success" | "error", updatedAt: number): void {
  const query = client.getQueryCache().build(client, { queryKey: key });
  query.setState(status === "error" ? { status, error: new Error("No scan"), errorUpdatedAt: updatedAt }
    : { status, data: {}, dataUpdatedAt: updatedAt });
}

function cuts(siteId: string, status: RadarTilts["tilts"][number]["status"]): RadarTilts {
  return { siteId, product: "reflectivity", checkedAt: secondTime, tilts: RADAR_TILTS.map(({ value }) => ({ tilt: value, status })) };
}

it("does not treat an earlier live failure as a historical failure for the same requested minute", () => {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  attempt(client, mapKeys.radarSite("reflectivity", "KTLX", secondTime, "0", true), "error", 20);
  const hook = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", secondTime, null), { wrapper });
  expect(hook.result.current.has("KTLX")).toBe(false);
  hook.unmount(); client.clear();
});

it("reacts to the latest live attempt per site, active failures and recovery", () => {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  attempt(client, mapKeys.radarSite("reflectivity", "KTLX", firstTime), "success", 10);
  const props = { history: null as string | null, active: "KACT" as string | null };
  const hook = renderHook(({ history, active }) => useUnavailableRadarSites(sites, "reflectivity", history, active), { wrapper, initialProps: props });
  try {
    expect([...hook.result.current].sort()).toEqual(["EMPTY", "KACT"]);
    const stable = hook.result.current;
    act(() => { client.setQueryData(["unrelated"], "updated"); });
    expect(hook.result.current).toBe(stable);
    act(() => { attempt(client, mapKeys.radarSite("reflectivity", "KTLX", secondTime), "error", 20); });
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { attempt(client, mapKeys.radarSite("reflectivity", "KTLX", firstTime), "success", 30); });
    expect(hook.result.current.has("KTLX")).toBe(false);
    hook.rerender({ history: null, active: null });
    expect([...hook.result.current]).toEqual(["EMPTY"]);
    act(() => { attempt(client, mapKeys.radarSite("reflectivity", "KTLX", secondTime), "error", 40); });
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { attempt(client, mapKeys.radarSite("reflectivity", "KTLX", firstTime), "success", 40); });
    expect(hook.result.current.has("KTLX")).toBe(false);
    act(() => { attempt(client, mapKeys.radarSite("reflectivity", "KTLX", secondTime), "error", 40); });
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { client.removeQueries({ queryKey: mapKeys.radarSite("reflectivity", "KTLX", secondTime), exact: true }); });
    expect(hook.result.current.has("KTLX")).toBe(false);
  } finally { hook.unmount(); client.clear(); }
});

it("scopes frame and tilt failures to each site's resolved product", () => {
  const client = createQueryClient();
  const catalog: RadarSite[] = [
    { id: "KTLX", longitude: 0, latitude: 0, products: ["reflectivity", "velocity"] },
    { id: "DESS", longitude: 1, latitude: 1, country: "DE", products: ["reflectivity"] },
  ];
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  attempt(client, mapKeys.radarSite("velocity", "KTLX", firstTime), "success", 10);
  attempt(client, mapKeys.radarSite("reflectivity", "KTLX", secondTime), "error", 20);
  attempt(client, mapKeys.radarSite("reflectivity", "DESS", firstTime), "error", 10);
  const hook = renderHook(() => useUnavailableRadarSites(catalog, "velocity", null, null), { wrapper });
  try {
    expect([...hook.result.current]).toEqual(["DESS"]);
    act(() => { attempt(client, mapKeys.radarSite("reflectivity", "DESS", secondTime), "success", 30); });
    expect(hook.result.current.size).toBe(0);
    act(() => { client.setQueryData(mapKeys.radarTilts("reflectivity", "KTLX", null), cuts("KTLX", "unavailable")); });
    act(() => { client.setQueryData(mapKeys.radarTilts("velocity", "DESS", null), cuts("DESS", "unavailable")); });
    expect(hook.result.current.size).toBe(0);
    act(() => { attempt(client, mapKeys.radarSite("velocity", "KTLX", secondTime), "error", 40); });
    expect([...hook.result.current]).toEqual(["KTLX"]);
    act(() => { client.setQueryData(mapKeys.radarTilts("reflectivity", "DESS", null), cuts("DESS", "unavailable")); });
    expect(hook.result.current.has("DESS")).toBe(true);
  } finally { hook.unmount(); client.clear(); }
});

it("observes a real automatic retry while TanStack stores the failure as a retry reason", async () => {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const key = mapKeys.radarSite("reflectivity", "KTLX", secondTime);
  let resolveScan: (value: object) => void = () => undefined;
  const scan = new Promise<object>((resolve) => { resolveScan = resolve; });
  let calls = 0, request = Promise.resolve({});
  const hook = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
  try {
    act(() => { request = client.query({ queryKey: key, queryFn: () => ++calls === 1 ? Promise.reject(new Error("Retry")) : scan, retry: 1, retryDelay: 0 }); });
    await waitFor(() => { expect(calls).toBe(2); });
    expect(client.getQueryState(key)).toMatchObject({ status: "pending", error: null, fetchFailureCount: 1 });
    await waitFor(() => { expect(hook.result.current.has("KTLX")).toBe(true); });
    await act(async () => { resolveScan({}); await request; });
    expect(hook.result.current.has("KTLX")).toBe(false);
  } finally { resolveScan({}); hook.unmount(); client.clear(); }
});

it("keeps failed pending attempts unavailable until they recover", () => {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const key = mapKeys.radarSite("reflectivity", "KTLX", secondTime);
  const query = client.getQueryCache().build(client, { queryKey: key });
  const hook = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
  try {
    expect(hook.result.current.has("KTLX")).toBe(false);
    act(() => { query.setState({ status: "pending", error: new Error("No scan"), errorUpdatedAt: 10 }); });
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { query.setState({ status: "pending", error: null, errorUpdateCount: 1, errorUpdatedAt: 10 }); });
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { query.setState({ status: "pending", error: null, errorUpdateCount: 0, errorUpdatedAt: 0, fetchFailureReason: new Error("Retrying"), fetchFailureCount: 1 }); });
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { query.setState({ status: "pending", error: null, errorUpdateCount: 0, errorUpdatedAt: 0, fetchFailureReason: null, fetchFailureCount: 0 }); });
    expect(hook.result.current.has("KTLX")).toBe(false);
    act(() => { attempt(client, key, "success", 20); });
    expect(hook.result.current.has("KTLX")).toBe(false);
  } finally { hook.unmount(); client.clear(); }
});

it("compares all-unavailable tilts with frame attempts, including equal timestamps", () => {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const key = mapKeys.radarSite("reflectivity", "KTLX", firstTime);
  const tiltKey = mapKeys.radarTilts("reflectivity", "KTLX", null);
  const tiltQuery = client.getQueryCache().build(client, { queryKey: tiltKey });
  const hook = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
  const unavailable = () => { tiltQuery.setState({ status: "success", data: cuts("KTLX", "unavailable"), dataUpdatedAt: 20 }); };
  try {
    act(unavailable);
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { attempt(client, key, "success", 30); });
    expect(hook.result.current.has("KTLX")).toBe(false);
    act(() => { attempt(client, key, "success", 20); });
    expect(hook.result.current.has("KTLX")).toBe(false);
    act(unavailable);
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { attempt(client, key, "success", 20); });
    expect(hook.result.current.has("KTLX")).toBe(false);
    act(() => { attempt(client, key, "error", 30); });
    expect(hook.result.current.has("KTLX")).toBe(true);
  } finally { hook.unmount(); client.clear(); }
});

it("preserves equal-time result order before mounting and between panel mounts", () => {
  const client = createQueryClient();
  const cacheSubscribe = vi.spyOn(client.getQueryCache(), "subscribe");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const frameKey = mapKeys.radarSite("reflectivity", "KTLX", firstTime);
  const tiltKey = mapKeys.radarTilts("reflectivity", "KTLX", null);
  const updatedAt = Date.now();
  const unavailable = () => { client.setQueryData(tiltKey, cuts("KTLX", "unavailable"), { updatedAt }); };
  const success = () => { client.setQueryData(frameKey, {}, { updatedAt }); };
  unavailable(); success();
  expect(client.getQueryState(tiltKey)?.dataUpdatedAt).toBe(updatedAt);
  expect(client.getQueryState(frameKey)?.dataUpdatedAt).toBe(updatedAt);
  const first = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
  const second = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
  try {
    expect(first.result.current.has("KTLX")).toBe(false);
    expect(second.result.current.has("KTLX")).toBe(false);
    expect(cacheSubscribe).not.toHaveBeenCalled();
    act(unavailable);
    expect(first.result.current.has("KTLX")).toBe(true);
    expect(second.result.current.has("KTLX")).toBe(true);
    act(success);
    expect(first.result.current.has("KTLX")).toBe(false);
    expect(second.result.current.has("KTLX")).toBe(false);
    first.unmount(); second.unmount();
    unavailable(); success();
    const remounted = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
    try { expect(remounted.result.current.has("KTLX")).toBe(false); } finally { remounted.unmount(); }
    expect(cacheSubscribe).not.toHaveBeenCalled();
  } finally { first.unmount(); second.unmount(); cacheSubscribe.mockRestore(); client.clear(); }
});

it("subscribes once when panels share a client created outside the factory", () => {
  const client = new QueryClient();
  const cacheSubscribe = vi.spyOn(client.getQueryCache(), "subscribe");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const first = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
  const second = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
  try {
    expect(cacheSubscribe).toHaveBeenCalledTimes(1);
    act(() => { client.setQueryData(mapKeys.radarTilts("reflectivity", "KTLX", null), cuts("KTLX", "unavailable")); });
    expect(first.result.current.has("KTLX")).toBe(true);
    expect(second.result.current.has("KTLX")).toBe(true);
    first.unmount(); second.unmount();
    const remounted = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
    try { expect(remounted.result.current.has("KTLX")).toBe(true); } finally { remounted.unmount(); }
    expect(cacheSubscribe).toHaveBeenCalledTimes(1);
  } finally { first.unmount(); second.unmount(); cacheSubscribe.mockRestore(); client.clear(); }
});

it("does not reorder equal-time results when a frame or tilt refresh merely starts", async () => {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const key = mapKeys.radarSite("reflectivity", "KTLX", firstTime);
  const tiltKey = mapKeys.radarTilts("reflectivity", "KTLX", null);
  const tiltQuery = client.getQueryCache().build(client, { queryKey: tiltKey });
  const hook = renderHook(() => useUnavailableRadarSites(sites, "reflectivity", null, null), { wrapper });
  let finishFrame: (value: object) => void = () => undefined;
  let finishTilts: (value: RadarTilts) => void = () => undefined;
  const frame = new Promise<object>((resolve) => { finishFrame = resolve; });
  const tilts = new Promise<RadarTilts>((resolve) => { finishTilts = resolve; });
  let frameRequest = Promise.resolve({}), tiltRequest = Promise.resolve(cuts("KTLX", "unavailable"));
  try {
    act(() => { attempt(client, key, "success", 20); });
    act(() => { tiltQuery.setState({ status: "success", data: cuts("KTLX", "unavailable"), dataUpdatedAt: 20 }); });
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { frameRequest = client.query({ queryKey: key, queryFn: () => frame, staleTime: 0 }).catch(() => ({})); });
    expect(hook.result.current.has("KTLX")).toBe(true);
    act(() => { attempt(client, key, "success", 20); });
    expect(hook.result.current.has("KTLX")).toBe(false);
    act(() => { tiltRequest = client.query({ queryKey: tiltKey, queryFn: () => tilts, staleTime: 0 }).catch(() => cuts("KTLX", "available")); });
    expect(hook.result.current.has("KTLX")).toBe(false);
    await act(async () => { finishFrame({}); finishTilts(cuts("KTLX", "available")); await Promise.all([frameRequest, tiltRequest]); });
  } finally { finishFrame({}); finishTilts(cuts("KTLX", "available")); hook.unmount(); client.clear(); }
});

it("matches historical frame times and only applies all-unavailable tilts for the active product and time", () => {
  const client = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  attempt(client, mapKeys.radarSite("reflectivity", "KTLX", firstTime), "error", 10);
  attempt(client, mapKeys.radarSite("velocity", "KTLX", secondTime), "success", 20);
  client.setQueryData(mapKeys.radarTilts("velocity", "KVEL", firstTime), cuts("KVEL", "unavailable"));
  client.setQueryData(mapKeys.radarTilts("reflectivity", "KOLD", secondTime), cuts("KOLD", "unavailable"));
  client.setQueryData(mapKeys.radarTilts("reflectivity", "KUNK", firstTime), cuts("KUNK", "unknown"));
  const initialProps: { history: string | null } = { history: firstTime };
  const hook = renderHook(({ history }) => useUnavailableRadarSites(sites, "reflectivity", history, null), { wrapper, initialProps });
  try {
    expect([...hook.result.current].sort()).toEqual(["EMPTY", "KTLX"]);
    act(() => { client.setQueryData(mapKeys.radarTilts("reflectivity", "KOLD", firstTime), cuts("KOLD", "unavailable")); });
    expect(hook.result.current.has("KOLD")).toBe(true);
    act(() => { client.setQueryData(mapKeys.radarTilts("reflectivity", "KOLD", firstTime), cuts("KOLD", "available")); });
    expect(hook.result.current.has("KOLD")).toBe(false);
    hook.rerender({ history: secondTime });
    expect(hook.result.current.has("KTLX")).toBe(false);
    expect(hook.result.current.has("KOLD")).toBe(true);
    hook.rerender({ history: null });
    expect(hook.result.current.has("KOLD")).toBe(false);
    act(() => { client.setQueryData(mapKeys.radarTilts("reflectivity", "KOLD", null), cuts("KOLD", "unavailable")); });
    expect(hook.result.current.has("KOLD")).toBe(true);
  } finally { hook.unmount(); client.clear(); }
});

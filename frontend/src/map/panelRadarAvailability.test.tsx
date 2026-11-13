// @vitest-environment jsdom
import radar from "@contracts/public/map/radar.shrf?url&inline";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { BackendFailure } from "../lib/backend/errors";
import { createTransport } from "../lib/backend/fixtureTransport";
import { createQueryClient, mapKeys } from "../lib/backend/queryOptions";
import { encodeShrf, parseRadarSiteFrame, parseShrf } from "../lib/contracts/frames";
import { useLiveTrackingStore } from "../stores/liveTrackingStore";
import { useMapStore } from "../stores/mapStore";
import type { RadarSiteFrame } from "../types/map";
import type { SceneData } from "./layers/registry";
import { usePanelScene } from "./panelScene";
import type { MapRuntime } from "./runtime/MapRuntime";

const savedMap = useMapStore.getState(), savedLive = useLiveTrackingStore.getState();
afterEach(() => { useMapStore.setState(savedMap, true); useLiveTrackingStore.setState(savedLive, true); vi.unstubAllGlobals(); });

it("propagates an active frame failure, decode failure, recovery, and mosaic selection to the scene", async () => {
  const now = Date.parse("2026-10-17T08:00:00Z");
  useLiveTrackingStore.setState({ now, selectedTime: null });
  useMapStore.setState({ view: "live", activePanel: 0 });
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite("KTLX");
  useMapStore.getState().setLayerVisible("radar", true);
  const decoded = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);
  const frame = encodeShrf({ ...decoded.header, kind: "radar-site", siteId: "KTLX", product: "reflectivity" }, decoded.body);
  let fails = true, delay = false;
  let resolveFrame: (value: ArrayBuffer) => void = () => undefined;
  const heldFrame = new Promise<ArrayBuffer>((resolve) => { resolveFrame = resolve; });
  const transport = createTransport(), call = transport.call.bind(transport);
  transport.call = (command, args) => command === "get_radar_site_frame"
    ? fails ? Promise.reject(new Error("Radar offline")) : delay ? heldFrame : Promise.resolve(frame) : call(command, args);
  const decode = vi.fn().mockRejectedValueOnce(new Error("Corrupt PNG")).mockResolvedValue({ width: 2, height: 2 });
  vi.stubGlobal("createImageBitmap", decode);
  const queryClient = createQueryClient(), client = new BackendClient(transport);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const scene: Partial<SceneData> = {};
  const runtime = { setScene: (patch: Partial<SceneData>) => { Object.assign(scene, patch); }, setStations: vi.fn() } as unknown as MapRuntime;
  const hook = renderHook(() => usePanelScene(runtime, 0), { wrapper });
  const key = mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T08:00:00Z");
  try {
    await waitFor(() => { expect(queryClient.getQueryState(key)?.status).toBe("error"); });
    await waitFor(() => { expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true); });
    await waitFor(() => { expect(hook.result.current.issues.some((issue) => issue.startsWith("Radar: Imagery could not be loaded"))).toBe(true); });
    expect(scene.selectedRadarSite).toBe("KTLX");
    fails = false; delay = true;
    let retry = Promise.resolve();
    act(() => { retry = queryClient.refetchQueries({ queryKey: key, exact: true }); });
    await waitFor(() => { expect(queryClient.getQueryState(key)).toMatchObject({ status: "pending", fetchStatus: "fetching", error: null, errorUpdateCount: 1 }); });
    await waitFor(() => { expect(hook.result.current.issues.some((issue) => issue.startsWith("Radar: Imagery could not be loaded"))).toBe(false); });
    expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true);
    await act(async () => { resolveFrame(frame); await retry; });
    delay = false;
    await waitFor(() => { expect(hook.result.current.issues.some((issue) => issue.includes("could not be decoded"))).toBe(true); });
    expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true);
    act(() => { hook.result.current.retry(); });
    await waitFor(() => { expect(scene.unavailableRadarSites?.has("KTLX")).toBe(false); });
    expect(scene.selectedRadarSite).toBe("KTLX");
    act(() => { useMapStore.getState().setRadarScope("mosaic"); });
    await waitFor(() => { expect(scene.selectedRadarSite).toBeNull(); });
  } finally { resolveFrame(frame); hook.unmount(); queryClient.clear(); }
});

it("keeps a failed site unavailable until its successful retry has decoded", async () => {
  useLiveTrackingStore.setState({ now: Date.parse("2026-10-17T08:00:00Z"), selectedTime: null });
  useMapStore.setState({ view: "live", activePanel: 0 });
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite("KTLX");
  useMapStore.getState().setLayerVisible("radar", true);
  const decoded = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);
  const frame = encodeShrf({ ...decoded.header, kind: "radar-site", siteId: "KTLX", product: "reflectivity" }, decoded.body);
  const bitmap = { width: 2, height: 2 } as ImageBitmap;
  let resolveDecode: (value: ImageBitmap) => void = () => undefined;
  const heldDecode = new Promise<ImageBitmap>((resolve) => { resolveDecode = resolve; });
  const decode = vi.fn().mockReturnValue(heldDecode);
  vi.stubGlobal("createImageBitmap", decode);
  let fails = true;
  const transport = createTransport(), call = transport.call.bind(transport);
  transport.call = (command, args) => command === "get_radar_site_frame"
    ? fails ? Promise.reject(new Error("Radar offline")) : Promise.resolve(frame) : call(command, args);
  const queryClient = createQueryClient(), client = new BackendClient(transport);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const scene: Partial<SceneData> = {};
  const runtime = { setScene: (patch: Partial<SceneData>) => { Object.assign(scene, patch); }, setStations: vi.fn() } as unknown as MapRuntime;
  const hook = renderHook(() => usePanelScene(runtime, 0), { wrapper });
  const key = mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T08:00:00Z");
  try {
    await waitFor(() => { expect(queryClient.getQueryState(key)?.status).toBe("error"); });
    await waitFor(() => { expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true); });
    fails = false;
    await act(async () => { await queryClient.refetchQueries({ queryKey: key, exact: true }); });
    await waitFor(() => { expect(decode).toHaveBeenCalled(); });
    expect(queryClient.getQueryState(key)).toMatchObject({ status: "success", errorUpdateCount: 1 });
    expect(scene.radar).toBeNull();
    expect(hook.result.current.issues.some((issue) => issue.startsWith("Radar:"))).toBe(false);
    expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true);
    await act(async () => { resolveDecode(bitmap); await heldDecode; });
    await waitFor(() => { expect(scene.radar?.image).toBe(bitmap); });
    expect(scene.unavailableRadarSites?.has("KTLX")).toBe(false);
  } finally { resolveDecode(bitmap); hook.unmount(); queryClient.clear(); }
});

it("keeps each panel's active site independent of other products, tilts, and stale live scans", async () => {
  useLiveTrackingStore.setState({ now: Date.parse("2026-10-17T08:00:00Z"), selectedTime: null });
  useMapStore.setState({ view: "live", activePanel: 0 });
  const state = useMapStore.getState();
  state.setPanelLayout(2);
  state.setRadarScope("site"); state.setRadarSite("KTLX"); state.setRadarProduct("reflectivity");
  state.setActivePanel(1);
  state.setRadarScope("site"); state.setRadarSite("KTLX"); state.setRadarProduct("velocity");
  const decoded = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);
  const frame = encodeShrf({ ...decoded.header, kind: "radar-site", siteId: "KTLX", product: "reflectivity" }, decoded.body);
  const transport = createTransport(), call = transport.call.bind(transport);
  transport.call = (command, args) => command === "get_radar_site_frame"
    ? args?.product === "velocity" ? Promise.reject(new Error("Velocity offline")) : Promise.resolve(frame) : call(command, args);
  vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 2, height: 2 }));
  const queryClient = createQueryClient(), client = new BackendClient(transport);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const scenes: Partial<SceneData>[] = [{}, {}];
  const runtime = (index: number) => ({ setScene: (patch: Partial<SceneData>) => { Object.assign(scenes[index] ?? {}, patch); }, setStations: vi.fn() }) as unknown as MapRuntime;
  const runtimes = [runtime(0), runtime(1)];
  const hook = renderHook(() => { usePanelScene(runtimes[0] ?? null, 0); usePanelScene(runtimes[1] ?? null, 1); }, { wrapper });
  const key = mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T08:00:00Z");
  try {
    await waitFor(() => { expect(queryClient.getQueryState(key)?.status).toBe("success"); });
    await waitFor(() => { expect(scenes[1]?.unavailableRadarSites?.has("KTLX")).toBe(true); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(false); });
    const stale = queryClient.getQueryCache().build(queryClient, { queryKey: mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T07:55:00Z", "1") });
    act(() => { stale.setState({ status: "error", error: new Error("Other tilt offline"), errorUpdatedAt: Date.now() + 1000 }); });
    expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(false);
    const active = queryClient.getQueryCache().find({ queryKey: key, exact: true });
    if (active === undefined) throw new Error("Missing active frame query");
    const previous = queryClient.getQueryData<RadarSiteFrame>(key);
    if (previous === undefined) throw new Error("Missing active frame");
    act(() => { queryClient.setQueryData(mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T07:55:00Z"), { ...previous, validTime: "2026-10-17T07:55:00Z" }); });
    act(() => { active.setState({ status: "pending", data: undefined, error: new Error("Retrying"), errorUpdatedAt: Date.now() + 2000 }); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(true); });
    act(() => { active.setState({ status: "pending", error: null, errorUpdateCount: 0, errorUpdatedAt: 0 }); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(true); });
    act(() => { active.setState({ status: "pending", error: null, errorUpdateCount: 1, errorUpdatedAt: Date.now() }); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(true); });
    act(() => { active.setState({ status: "pending", error: null, errorUpdateCount: 0, errorUpdatedAt: 0 }); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(true); });
    act(() => { active.setState({ status: "pending", error: null, errorUpdateCount: 0, errorUpdatedAt: 0, fetchFailureReason: new Error("Retrying"), fetchFailureCount: 1 }); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(true); });
    act(() => { active.setState({ status: "pending", error: null, errorUpdateCount: 0, errorUpdatedAt: 0, fetchFailureReason: null, fetchFailureCount: 0 }); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(true); });
    await act(async () => { await queryClient.refetchQueries({ queryKey: key, exact: true }); });
    await waitFor(() => { expect(queryClient.getQueryState(key)?.status).toBe("success"); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(false); });
    expect(scenes[1]?.unavailableRadarSites?.has("KTLX")).toBe(true);
    act(() => { active.setState({ status: "pending", data: undefined, error: null, errorUpdateCount: 0, fetchFailureReason: null, fetchFailureCount: 1 }); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(true); });
    act(() => { useMapStore.getState().setActivePanel(0); useMapStore.getState().setRadarTilt("1"); });
    await waitFor(() => { expect(queryClient.getQueryState(mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T08:00:00Z", "1"))?.status).toBe("success"); });
    await waitFor(() => { expect(scenes[0]?.unavailableRadarSites?.has("KTLX")).toBe(false); });
    expect(scenes[1]?.unavailableRadarSites?.has("KTLX")).toBe(true);
  } finally { hook.unmount(); queryClient.clear(); }
});

it("keeps a failed site unavailable while its older bitmap is displayed during retry decoding", async () => {
  useLiveTrackingStore.setState({ now: Date.parse("2026-10-17T08:00:00Z"), selectedTime: null });
  useMapStore.setState({ view: "live", activePanel: 0 });
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite("KTLX");
  useMapStore.getState().setLayerVisible("radar", true);
  const decoded = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);
  const header = { ...decoded.header, kind: "radar-site", siteId: "KTLX", product: "reflectivity" };
  const older = encodeShrf({ ...header, validTime: "2026-10-17T07:55:00Z" }, decoded.body);
  const newer = encodeShrf({ ...header, validTime: "2026-10-17T08:00:00Z" }, decoded.body);
  const oldBitmap = { width: 2, height: 2 } as ImageBitmap, newBitmap = { width: 3, height: 3 } as ImageBitmap;
  const nextBitmap = { width: 4, height: 4 } as ImageBitmap;
  let resolveDecode: (value: ImageBitmap) => void = () => undefined;
  const heldDecode = new Promise<ImageBitmap>((resolve) => { resolveDecode = resolve; });
  let resolveNextDecode: (value: ImageBitmap) => void = () => undefined;
  const nextDecode = new Promise<ImageBitmap>((resolve) => { resolveNextDecode = resolve; });
  const decode = vi.fn().mockResolvedValueOnce(oldBitmap).mockReturnValueOnce(heldDecode).mockReturnValueOnce(nextDecode);
  vi.stubGlobal("createImageBitmap", decode);
  let outcome: "older" | "failure" | "newer" = "older";
  const transport = createTransport(), call = transport.call.bind(transport);
  transport.call = (command, args) => command === "get_radar_site_frame"
    ? outcome === "failure" ? Promise.reject(new Error("Radar offline")) : Promise.resolve(outcome === "older" ? older : newer) : call(command, args);
  const queryClient = createQueryClient(), client = new BackendClient(transport);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const scene: Partial<SceneData> = {};
  const runtime = { setScene: (patch: Partial<SceneData>) => { Object.assign(scene, patch); }, setStations: vi.fn() } as unknown as MapRuntime;
  const hook = renderHook(() => usePanelScene(runtime, 0), { wrapper });
  const key = mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T08:00:00Z");
  try {
    await waitFor(() => { expect(scene.radar?.image).toBe(oldBitmap); });
    outcome = "failure";
    await act(async () => { await queryClient.refetchQueries({ queryKey: key, exact: true }); });
    await waitFor(() => { expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true); });
    outcome = "newer";
    await act(async () => { await queryClient.refetchQueries({ queryKey: key, exact: true }); });
    await waitFor(() => { expect(decode).toHaveBeenCalledTimes(2); });
    expect(queryClient.getQueryState(key)).toMatchObject({ status: "success", errorUpdateCount: 1, fetchFailureReason: null });
    expect(scene.radar?.image).toBe(oldBitmap);
    expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true);
    await act(async () => { resolveDecode(newBitmap); await heldDecode; });
    await waitFor(() => { expect(scene.radar?.image).toBe(newBitmap); });
    await waitFor(() => { expect(scene.unavailableRadarSites?.has("KTLX")).toBe(false); });
    await act(async () => { await queryClient.refetchQueries({ queryKey: key, exact: true }); });
    await waitFor(() => { expect(decode).toHaveBeenCalledTimes(3); });
    expect(scene.radar?.image).toBe(newBitmap);
    expect(scene.unavailableRadarSites?.has("KTLX")).toBe(false);
    await act(async () => { resolveNextDecode(nextBitmap); await nextDecode; });
    await waitFor(() => { expect(scene.radar?.image).toBe(nextBitmap); });
  } finally { resolveDecode(newBitmap); resolveNextDecode(nextBitmap); hook.unmount(); queryClient.clear(); }
});

it("keeps an automatically retried site unavailable after fetch success until decoding finishes", async () => {
  useLiveTrackingStore.setState({ now: Date.parse("2026-10-17T08:00:00Z"), selectedTime: null });
  useMapStore.setState({ view: "live", activePanel: 0 });
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite("KTLX");
  useMapStore.getState().setLayerVisible("radar", true);
  const decoded = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer);
  const frame = encodeShrf({ ...decoded.header, kind: "radar-site", siteId: "KTLX", product: "reflectivity" }, decoded.body);
  const bitmap = { width: 2, height: 2 } as ImageBitmap;
  let resolveDecode: (value: ImageBitmap) => void = () => undefined;
  const heldDecode = new Promise<ImageBitmap>((resolve) => { resolveDecode = resolve; });
  const decode = vi.fn().mockReturnValue(heldDecode);
  vi.stubGlobal("createImageBitmap", decode);
  let resolveFrame: (value: RadarSiteFrame) => void = () => undefined;
  const heldFrame = new Promise<RadarSiteFrame>((resolve) => { resolveFrame = resolve; });
  const queryClient = createQueryClient(), client = new BackendClient(createTransport());
  const request = vi.spyOn(client, "getRadarSiteFrame").mockRejectedValueOnce(new BackendFailure({ code: "OVERLAY_FETCH_FAILED", message: "Radar offline", retryable: true })).mockReturnValue(heldFrame);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const scene: Partial<SceneData> = {};
  const runtime = { setScene: (patch: Partial<SceneData>) => { Object.assign(scene, patch); }, setStations: vi.fn() } as unknown as MapRuntime;
  const hook = renderHook(() => usePanelScene(runtime, 0), { wrapper });
  const key = mapKeys.radarSite("reflectivity", "KTLX", "2026-10-17T08:00:00Z");
  try {
    await waitFor(() => { expect(request).toHaveBeenCalledTimes(2); }, { timeout: 3000 });
    expect(queryClient.getQueryState(key)).toMatchObject({ status: "pending", errorUpdateCount: 0, fetchFailureCount: 1 });
    expect(queryClient.getQueryState(key)?.fetchFailureReason).not.toBeNull();
    await waitFor(() => { expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true); });
    await act(async () => { resolveFrame(parseRadarSiteFrame(frame)); await heldFrame; });
    await waitFor(() => { expect(decode).toHaveBeenCalled(); });
    expect(queryClient.getQueryState(key)).toMatchObject({ status: "success", errorUpdateCount: 0, fetchFailureCount: 0, fetchFailureReason: null });
    expect(scene.radar).toBeNull();
    expect(scene.unavailableRadarSites?.has("KTLX")).toBe(true);
    await act(async () => { resolveDecode(bitmap); await heldDecode; });
    await waitFor(() => { expect(scene.radar?.image).toBe(bitmap); });
    await waitFor(() => { expect(scene.unavailableRadarSites?.has("KTLX")).toBe(false); });
  } finally { resolveFrame(parseRadarSiteFrame(frame)); resolveDecode(bitmap); hook.unmount(); queryClient.clear(); }
});

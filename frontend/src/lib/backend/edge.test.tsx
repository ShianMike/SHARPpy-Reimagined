// @vitest-environment jsdom
import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { version } from "../../../package.json";
import { HealthIndicator } from "../../features/toolbar/HealthIndicator";
import { renderWithBackend } from "../../test/backendHarness";
import { fetchHostedBackendWithEdge } from "./edge";
import { createTransport } from "./fixtureTransport";

const releaseRoot = "https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v";
const [major = "0", minor = "0"] = version.split(".");
const next = `${major}.${String(Number(minor) + 1)}.0`;
const previous = Number(minor) > 0 ? `${major}.${String(Number(minor) - 1)}.0` : "0.0.0";
const release = { version: next, url: releaseRoot + next };
const status = {
  schemaVersion: 1,
  checkedAtMs: Date.now(),
  stale: false,
  origin: { state: "up", status: 200, version },
  models: { state: "unknown", source: "origin" },
  radar: { state: "unavailable", source: "probe" },
  release,
};

function mockEdge(snapshot: unknown = status, config: unknown = { schemaVersion: 1, routePublicReads: true }) {
  vi.stubEnv("MODE", "live");
  vi.stubEnv("VITE_SHARPPY_EDGE_PATH", "/__edge");
  const request = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url) =>
    Promise.resolve(Response.json(url === "/__edge/config" ? config : snapshot)));
  vi.stubGlobal("fetch", request);
  return request;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it.each([
  { mode: "live", path: undefined, native: false },
  { mode: "live", path: "//edge.example/__edge", native: false },
  { mode: "fixture", path: "/__edge", native: false },
  { mode: "live", path: "/__edge", native: true },
])("keeps the edge disabled with $mode, $path, native=$native", ({ mode, path, native }) => {
  vi.stubEnv("MODE", mode);
  vi.stubEnv("VITE_SHARPPY_EDGE_PATH", path);
  if (native) vi.stubGlobal("__TAURI_INTERNALS__", {});
  const request = vi.fn();
  vi.stubGlobal("fetch", request);
  renderWithBackend(<HealthIndicator />, createTransport());
  expect(screen.queryByRole("region", { name: "Hosted services" })).toBeNull();
  expect(request).not.toHaveBeenCalled();
});

it("shares one visible check across status views and keeps local health available", async () => {
  const request = mockEdge();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  renderWithBackend(<><HealthIndicator /><HealthIndicator /></>, createTransport());
  expect(request).not.toHaveBeenCalled();
  visibility.mockReturnValue("visible");
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });

  const links = await screen.findAllByRole("link", { name: `Release ${next} available` });
  expect(links.map((link) => link.getAttribute("href"))).toEqual([release.url, release.url]);
  expect(screen.getAllByText("Model probe unknown")).toHaveLength(2);
  expect(screen.getAllByText("Radar probe unavailable")).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "Backend info" })).toHaveLength(2);
  expect(request.mock.calls.map(([url]) => url)).toEqual(["/__edge/config", "/__edge/status"]);
});

it("checks every five minutes while visible and expires permission after ten minutes without a refresh", async () => {
  vi.useFakeTimers();
  const request = mockEdge();
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  renderWithBackend(<HealthIndicator />, createTransport());
  await act(async () => { await vi.advanceTimersByTimeAsync(299_999); });
  expect(request.mock.calls.map(([url]) => url)).toEqual(["/__edge/config", "/__edge/status"]);
  await act(async () => { await vi.advanceTimersByTimeAsync(2); });
  expect(request.mock.calls.map(([url]) => url)).toEqual([
    "/__edge/config", "/__edge/status", "/__edge/config", "/__edge/status",
  ]);

  visibility.mockReturnValue("hidden");
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  const init = { method: "POST", body: '{"id":1,"method":"get_map_catalog","params":{}}' } as const;
  await act(async () => { await vi.advanceTimersByTimeAsync(599_998); });
  await fetchHostedBackendWithEdge("get_map_catalog", init);
  expect(request).toHaveBeenLastCalledWith("/__edge/backend", expect.objectContaining(init));
  await act(async () => { await vi.advanceTimersByTimeAsync(2); });
  await fetchHostedBackendWithEdge("get_map_catalog", init);
  expect(request).toHaveBeenLastCalledWith("/__backend", init);
  expect(request).toHaveBeenCalledTimes(6);
});

it.each([
  { ...status, stale: true },
  { ...status, release: { version, url: releaseRoot + version } },
  { ...status, release: { version: previous, url: releaseRoot + previous } },
  { ...status, release: { ...release, url: release.url + "?download=1" } },
  { ...status, release: { ...release, url: "https://edge.example/release" } },
])("shows status without linking an unverified, stale or installed release %#", async (snapshot) => {
  mockEdge(snapshot);
  renderWithBackend(<HealthIndicator />, createTransport());
  await screen.findByText(snapshot.stale ? "Last reported" : "Origin up");
  expect(screen.queryByRole("link")).toBeNull();
});

it("keeps malformed status and config separate from backend health and routing", async () => {
  const request = mockEdge({ ...status, schemaVersion: 2 }, { schemaVersion: 1, routePublicReads: "true" });
  renderWithBackend(<HealthIndicator />, createTransport());
  await screen.findByText("Hosted service status unavailable");
  expect(screen.getByRole("button", { name: "Backend info" })).toBeTruthy();
  const init = { method: "POST", body: '{"id":1,"method":"get_map_catalog","params":{}}' } as const;
  await fetchHostedBackendWithEdge("get_map_catalog", init);
  expect(request).toHaveBeenLastCalledWith("/__backend", init);
});

it("routes only allowed commands and falls back only on a Worker-confirmed pre-forward failure", async () => {
  const request = mockEdge();
  renderWithBackend(<HealthIndicator />, createTransport());
  await screen.findByText("Origin up");
  request.mockClear();

  const init = { method: "POST", body: '{"id":1,"method":"get_radar_frame","params":{}}' } as const;
  const notForwarded = { "X-Sharppy-Edge-Route": "not-forwarded" };
  const replies = [
    Response.json({ jobId: "a".repeat(32) }, { status: 202, headers: { "X-Sharppy-Edge-Route": "routed" } }),
    ...[403, 429].map((code) => Response.json({ code }, { status: code, headers: notForwarded })),
    ...[404, 502, 503].map((code) => Response.json({ code }, { status: code })),
    Response.json({ code: 503 }, { status: 503, headers: { "X-Sharppy-Edge-Route": "routed" } }),
    new Response(new Uint8Array([1, 2, 3]), { headers: { "Content-Type": "application/octet-stream" } }),
  ];
  for (const response of replies) {
    request.mockResolvedValueOnce(response);
    await expect(fetchHostedBackendWithEdge("get_radar_frame", init)).resolves.toBe(response);
  }
  expect(request).toHaveBeenCalledTimes(replies.length);
  expect(request).toHaveBeenLastCalledWith("/__edge/backend", {
    ...init, credentials: "same-origin", mode: "same-origin", redirect: "error",
  });

  const modelStatus = { method: "POST", body: '{"id":1,"method":"get_model_status","params":{}}' } as const;
  await fetchHostedBackendWithEdge("get_model_status", modelStatus);
  expect(request).toHaveBeenLastCalledWith("/__backend", modelStatus);

  for (const code of [404, 503]) {
    const direct = Response.json({ sites: [] });
    request.mockClear();
    request.mockResolvedValueOnce(Response.json({ code }, { status: code, headers: notForwarded }));
    request.mockResolvedValueOnce(direct);
    await expect(fetchHostedBackendWithEdge("get_radar_frame", init)).resolves.toBe(direct);
    expect(request.mock.calls.map(([url]) => url)).toEqual(["/__edge/backend", "/__backend"]);
    expect(request).toHaveBeenLastCalledWith("/__backend", init);
  }

  request.mockClear();
  request.mockRejectedValueOnce(new TypeError("Connection closed"));
  await expect(fetchHostedBackendWithEdge("get_radar_frame", init)).rejects.toThrow("Connection closed");
  expect(request).toHaveBeenCalledOnce();
});

import { queryOptions, useQuery } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { version as installedVersion } from "../../../package.json";
import {
  expectBoolean, expectInteger, expectNullable, expectObject, expectOneOf, expectString, isPlainObject,
} from "../contracts/guards";
import type { CommandName } from "./transport";

const EDGE_REFRESH_MS = 5 * 60_000;
const EDGE_TIMEOUT_MS = 5_000;
const EDGE_PUBLIC_READS: ReadonlySet<CommandName> = new Set([
  "get_model_catalog",
  "get_map_catalog",
  "get_weather_field",
  "get_field_contours",
  "get_radar_sites",
  "get_radar_tilts",
  "get_radar_frame",
  "get_radar_site_frame",
  "get_station_catalog",
]);

type ProviderStatus = { state: "available" | "unavailable" | "unknown"; source: string };
type Release = { version: string; url: string };

interface EdgeStatus {
  checkedAtMs: number | null;
  stale: boolean;
  origin: { state: "up" | "down" | "unknown"; status: number | null; version: string | null };
  models: ProviderStatus;
  radar: ProviderStatus;
  release: Release | null;
}

// The hosted transport can read the latest permission without starting a second query.
let routing: { path: string; expiresAtMs: number } | null = null;

export function browserEdgePath(): string | null {
  if (import.meta.env.MODE !== "live" || typeof window === "undefined" || "__TAURI_INTERNALS__" in window) return null;
  const path: unknown = import.meta.env.VITE_SHARPPY_EDGE_PATH;
  return typeof path === "string" && /^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*\/?$/u.test(path)
    ? path.replace(/\/$/u, "")
    : null;
}

function parseRouting(value: unknown): boolean {
  const config = expectObject(value, "config");
  expectOneOf(config.schemaVersion, "config.schemaVersion", [1]);
  return expectBoolean(config.routePublicReads, "config.routePublicReads");
}

function parseProvider(value: unknown, path: string): ProviderStatus {
  const provider = expectObject(value, path);
  return {
    state: expectOneOf(provider.state, `${path}.state`, ["available", "unavailable", "unknown"]),
    source: expectString(provider.source, `${path}.source`, { maxLength: 120 }),
  };
}

function newerRelease(value: unknown): Release | null {
  if (!isPlainObject(value) || typeof value.version !== "string"
    || !/^(0|[1-9]\d{0,9})\.(0|[1-9]\d{0,9})\.(0|[1-9]\d{0,9})$/u.test(value.version)) return null;

  const version = value.version;
  const url = `https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v${version}`;
  if (value.url !== url) return null;
  const installed = installedVersion.split(".").map(Number);
  for (const [index, component] of version.split(".").entries()) {
    const difference = Number(component) - (installed[index] ?? 0);
    if (difference !== 0) return difference > 0 ? { version, url } : null;
  }
  return null;
}

function parseStatus(value: unknown): EdgeStatus {
  const status = expectObject(value, "status");
  expectOneOf(status.schemaVersion, "status.schemaVersion", [1]);
  const origin = expectObject(status.origin, "status.origin");
  return {
    checkedAtMs: expectNullable(status.checkedAtMs, "status.checkedAtMs",
      (value, path) => expectInteger(value, path, { min: 0, max: 8.64e15 })),
    stale: expectBoolean(status.stale, "status.stale"),
    origin: {
      state: expectOneOf(origin.state, "status.origin.state", ["up", "down", "unknown"]),
      status: expectNullable(origin.status, "status.origin.status",
        (value, path) => expectInteger(value, path, { min: 100, max: 599 })),
      version: expectNullable(origin.version, "status.origin.version",
        (value, path) => expectString(value, path, { maxLength: 64 })),
    },
    models: parseProvider(status.models, "status.models"),
    radar: parseProvider(status.radar, "status.radar"),
    release: newerRelease(status.release),
  };
}

async function readEdge<T>(url: string, parse: (value: unknown) => T): Promise<T | null> {
  const response = await fetch(url, {
    credentials: "same-origin", mode: "same-origin", redirect: "error",
    cache: "no-store", signal: AbortSignal.timeout(EDGE_TIMEOUT_MS),
  });
  if (!response.ok) return null;
  const raw: unknown = await response.json();
  return parse(raw);
}

async function loadEdgeStatus(path: string): Promise<EdgeStatus | null> {
  const config = readEdge(`${path}/config`, parseRouting).catch(() => null).then((enabled) => {
    routing = enabled === true ? { path, expiresAtMs: Date.now() + 2 * EDGE_REFRESH_MS } : null;
  });
  const status = readEdge(`${path}/status`, parseStatus).catch(() => null);
  const [, result] = await Promise.all([config, status]);
  return result;
}

export function edgeStatusQuery(path: string) {
  return queryOptions({
    queryKey: ["hosted-edge", path],
    queryFn: () => loadEdgeStatus(path),
    networkMode: "online",
    retry: false,
    staleTime: EDGE_REFRESH_MS,
  });
}

function subscribeVisibility(onChange: () => void): () => void {
  document.addEventListener("visibilitychange", onChange);
  return () => { document.removeEventListener("visibilitychange", onChange); };
}

function visible(): boolean {
  return document.visibilityState === "visible";
}

export function EdgeStatusMonitor({ path }: { path: string }): null {
  const isVisible = useSyncExternalStore(subscribeVisibility, visible);
  useQuery({
    ...edgeStatusQuery(path),
    enabled: isVisible,
    refetchInterval: EDGE_REFRESH_MS,
    refetchIntervalInBackground: false,
  });
  return null;
}

/** Route only the initial hosted POST. The caller still owns response decoding and origin jobs. */
export async function fetchHostedBackendWithEdge(
  command: CommandName,
  init: RequestInit & { method: "POST"; body: string },
): Promise<Response> {
  const path = browserEdgePath();
  if (path !== null && EDGE_PUBLIC_READS.has(command)
    && routing?.path === path && routing.expiresAtMs > Date.now()) {
    const response = await fetch(`${path}/backend`, { ...init, credentials: "same-origin", mode: "same-origin", redirect: "error" });
    // The Worker explicitly confirms that an origin request was never sent.
    if ((response.status !== 404 && response.status !== 503)
      || response.headers.get("X-Sharppy-Edge-Route") !== "not-forwarded") return response;
    void response.body?.cancel().catch(() => undefined);
  }
  return fetch("/__backend", init);
}

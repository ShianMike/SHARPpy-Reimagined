import catalogUnavailable from "@contracts/public/errors/CATALOG_UNAVAILABLE.json";
import healthOk from "@contracts/public/health.ok.json";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AnyBackendError } from "../../types/contracts";
import { BackendClient } from "./client";
import { BackendFailure } from "./errors";
import { CATALOG_ERROR_FAILURES, SLOW_CATALOG_MS, createTransport } from "./fixtureTransport";
import { retryDelayMs, shouldRetry } from "./queryOptions";
import type { CommandName, Transport } from "./transport";

type Script = (command: CommandName) => Promise<unknown>;

/** A scripted transport: each call runs the script; listen keeps the handler. */
function scripted(script: Script, listenResult?: Promise<() => void>) {
  let handler: ((payload: unknown) => void) | null = null;
  const transport: Transport = {
    call: (command) => script(command),
    listen: (_event, next) => {
      handler = next;
      return listenResult ?? Promise.resolve(() => undefined);
    },
  };
  return { transport, emit: (payload: unknown) => handler?.(payload) };
}

function rejecting(value: unknown): Script {
  // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- raw transport rejections under test
  return () => Promise.reject(value);
}

async function failureOf(promise: Promise<unknown>): Promise<AnyBackendError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(BackendFailure);
    return (error as BackendFailure).error;
  }
  throw new Error("expected the call to reject");
}

afterEach(() => {
  vi.useRealTimers();
});

describe("BackendClient error mapping", () => {
  it("passes a BackendError rejection through as BackendFailure with one warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = new BackendClient(scripted(rejecting(catalogUnavailable)).transport);
    const error = await failureOf(client.getStationCatalog());
    expect(error).toEqual(catalogUnavailable);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]?.[0])).toContain("CATALOG_UNAVAILABLE in get_station_catalog");
  });

  it.each([
    ["a string", "command get_station_catalog not allowed by ACL"],
    ["an Error without a code", new Error("IPC channel closed")],
    ["undefined", undefined],
  ])("maps %s to TRANSPORT_FAILED with one warning", async (_label, reason) => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = new BackendClient(scripted(rejecting(reason)).transport);
    const error = await failureOf(client.getBackendHealth());
    expect(error).toMatchObject({
      code: "TRANSPORT_FAILED",
      retryable: false,
      context: { operation: "get_backend_health" },
    });
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["an unknown code", { code: "PYTHON_EXPLODED", message: "Something happened.", retryable: false }, "error.code"],
    ["a missing retryable", { code: "CATALOG_UNAVAILABLE", message: "x" }, "error.retryable"],
  ])("maps an error object with %s to CONTRACT_VIOLATION and logs the path once", async (_label, reason, path) => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = new BackendClient(scripted(rejecting(reason)).transport);
    const error = await failureOf(client.getStationCatalog());
    expect(error.code).toBe("CONTRACT_VIOLATION");
    expect(error.details).toMatchObject({ path });
    expect(warn).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(warn.mock.calls[0])).toContain(path);
  });

  it("maps a payload that fails its parser to CONTRACT_VIOLATION", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = new BackendClient(scripted(() => Promise.resolve({ ...healthOk, bridge: { status: "sleeping" } })).transport);
    const error = await failureOf(client.getBackendHealth());
    expect(error).toMatchObject({ code: "CONTRACT_VIOLATION", details: { path: "bridge.status" } });
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("parses on every call", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    let calls = 0;
    const client = new BackendClient(
      scripted(() => {
        calls += 1;
        return Promise.resolve(calls === 1 ? healthOk : { ...healthOk, checkedAt: "yesterday" });
      }).transport,
    );
    await expect(client.getBackendHealth()).resolves.toEqual(healthOk);
    const error = await failureOf(client.getBackendHealth());
    expect(error).toMatchObject({ code: "CONTRACT_VIOLATION", details: { path: "checkedAt" } });
  });

  it("validates event payloads and drops invalid ones with one warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { transport, emit } = scripted(() => Promise.resolve(null));
    const client = new BackendClient(transport);
    const received: unknown[] = [];
    await client.onBridgeStatus((status) => received.push(status));
    emit(healthOk.bridge);
    emit({ status: "up", pid: 1 });
    expect(received).toEqual([healthOk.bridge]);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]?.[0])).toContain("CONTRACT_VIOLATION in bridge-status-changed");
  });

  it("rejects onBridgeStatus with TRANSPORT_FAILED when listen fails, without logging", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { transport } = scripted(
      () => Promise.resolve(null),
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors -- Tauri rejects with strings
      Promise.reject("event.listen not allowed"),
    );
    const error = await failureOf(new BackendClient(transport).onBridgeStatus(() => undefined));
    expect(error.code).toBe("TRANSPORT_FAILED");
    expect(warn).not.toHaveBeenCalled();
  });

  it("parses the restart_backend status", async () => {
    const client = new BackendClient(scripted(() => Promise.resolve(healthOk.bridge)).transport);
    await expect(client.restartBackend()).resolves.toEqual(healthOk.bridge);
  });
});

describe("retry policy", () => {
  const retryable = new BackendFailure({ code: "CATALOG_UNAVAILABLE", message: "x", retryable: true });
  const fatal = new BackendFailure({ code: "PYTHON_NOT_FOUND", message: "x", retryable: false });

  it("retries only a retryable BackendError, at most twice, after 1 s and 2 s", () => {
    expect(shouldRetry(0, retryable)).toBe(true);
    expect(shouldRetry(1, retryable)).toBe(true);
    expect(shouldRetry(2, retryable)).toBe(false);
    expect(shouldRetry(0, fatal)).toBe(false);
    expect(shouldRetry(0, new Error("other"))).toBe(false);
    expect([retryDelayMs(0), retryDelayMs(1)]).toEqual([1000, 2000]);
  });
});

describe("fixture transport scenarios through the real client", () => {
  const clientFor = (scenario: string | null) => new BackendClient(createTransport(scenario));

  it("serves health.ok and the golden 933-station catalogue by default", async () => {
    const client = clientFor(null);
    expect((await client.getBackendHealth()).bridge.status).toBe("up");
    const catalog = await client.getStationCatalog();
    expect(catalog.stations).toHaveLength(933);
    expect(catalog.origin).toBe("bundled");
    expect((await client.restartBackend()).status).toBe("up");
  });

  it("python-unavailable: unavailable health and PYTHON_NOT_FOUND for the catalogue", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = clientFor("python-unavailable");
    const health = await client.getBackendHealth();
    expect(health.bridge).toMatchObject({ status: "unavailable", error: { code: "PYTHON_NOT_FOUND" } });
    expect(health.python).toBeNull();
    expect((await failureOf(client.getStationCatalog())).code).toBe("PYTHON_NOT_FOUND");
  });

  it("catalog-error: three CATALOG_UNAVAILABLE rejections, then the golden catalogue", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = clientFor("catalog-error");
    for (let attempt = 0; attempt < CATALOG_ERROR_FAILURES; attempt += 1) {
      const error = await failureOf(client.getStationCatalog());
      expect(error).toMatchObject({ code: "CATALOG_UNAVAILABLE", retryable: true });
    }
    expect((await client.getStationCatalog()).stations).toHaveLength(933);
    expect(warn).toHaveBeenCalledTimes(3);
  });

  it("seed-fallback: the 10-record seed catalogue", async () => {
    const catalog = await clientFor("seed-fallback").getStationCatalog();
    expect(catalog.stations).toHaveLength(10);
    expect(catalog.origin).toBe("seedFallback");
  });

  it("restarting: health reports a restarting bridge", async () => {
    const health = await clientFor("restarting").getBackendHealth();
    expect(health.bridge).toMatchObject({ status: "restarting", attempt: 1 });
  });

  it("slow-catalog: the golden catalogue after 1.5 s", async () => {
    vi.useFakeTimers();
    let settled = false;
    const pending = clientFor("slow-catalog")
      .getStationCatalog()
      .then((catalog) => {
        settled = true;
        return catalog;
      });
    await vi.advanceTimersByTimeAsync(SLOW_CATALOG_MS - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect((await pending).stations).toHaveLength(933);
  });

  it("health-error: health rejects with INTERNAL, the catalogue still loads", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = clientFor("health-error");
    expect(await failureOf(client.getBackendHealth())).toMatchObject({ code: "INTERNAL", retryable: false });
    expect((await client.getStationCatalog()).stations).toHaveLength(933);
  });

  it("basemap-missing serves the defaults without an error", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const client = clientFor("basemap-missing");
    expect((await client.getBackendHealth()).bridge.status).toBe("up");
    expect((await client.getStationCatalog()).stations).toHaveLength(933);
    expect(error).not.toHaveBeenCalled();
  });

  it("an unknown scenario logs one console.error and serves the defaults", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const client = clientFor("no-such-scenario");
    expect(error).toHaveBeenCalledTimes(1);
    expect((await client.getStationCatalog()).stations).toHaveLength(933);
  });

  it("returns fresh copies, so callers cannot alias the fixtures", async () => {
    const transport = createTransport(null);
    const first = await transport.call("get_station_catalog");
    const second = await transport.call("get_station_catalog");
    expect(first).toEqual(second);
    expect(first).not.toBe(second);
  });
});

// @vitest-environment jsdom
import healthOk from "@contracts/public/health.ok.json";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackendProvider, useBackendHealth } from "./BackendProvider";
import { BackendClient } from "./client";
import { QUERY_KEYS, createQueryClient } from "./queryOptions";
import type { Transport } from "./transport";
import { parseSoundingSummary } from "../contracts/soundings";
import { useSoundingStore } from "../../stores/soundingStore";
import { INITIAL_VIEW } from "../../features/sounding/viewHistory";

const STARTING = { status: "starting", since: "2026-10-04T01:02:00Z" };
const healthWith = (bridge: unknown): unknown => ({ ...healthOk, bridge, python: null, scientificBackend: null });

/** A scripted transport whose listen resolves only when the test says so. */
function controlledTransport(healthReplies: unknown[]) {
  let handler: ((payload: unknown) => void) | null = null;
  let resolveListen: (unlisten: () => void) => void = () => undefined;
  let rejectListen: (reason: unknown) => void = () => undefined;
  const unlisten = vi.fn();
  const listening = { registered: false };
  let healthCalls = 0;
  const transport: Transport = {
    call: (command) => {
      if (command !== "get_backend_health") {
        return Promise.resolve(null);
      }
      const reply = healthReplies[Math.min(healthCalls, healthReplies.length - 1)];
      healthCalls += 1;
      return Promise.resolve(reply);
    },
    listen: (_event, next) =>
      new Promise((resolve, reject) => {
        resolveListen = (fn) => {
          handler = next;
          listening.registered = true;
          resolve(fn);
        };
        rejectListen = reject;
      }),
  };
  return {
    transport,
    unlisten,
    healthCalls: () => healthCalls,
    resolveListen: () => {
      resolveListen(unlisten);
    },
    rejectListen: (reason: unknown) => {
      rejectListen(reason);
    },
    /** Delivers an event only once the handler is registered, as Tauri does. */
    emit: (payload: unknown) => {
      if (listening.registered) {
        handler?.(payload);
      }
    },
  };
}

function HealthProbe(): ReactNode {
  const health = useBackendHealth();
  return <output data-testid="bridge">{health.data?.bridge.status ?? "none"}</output>;
}

function setup(transport: Transport, strict = false) {
  const queryClient = createQueryClient();
  const tree = (
    <QueryClientProvider client={queryClient}>
      <BackendProvider client={new BackendClient(transport)}>
        <HealthProbe />
      </BackendProvider>
    </QueryClientProvider>
  );
  const view = render(strict ? <StrictMode>{tree}</StrictMode> : tree);
  return { queryClient, view };
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const bridgeText = (): string => screen.getByTestId("bridge").textContent;

beforeEach(() => {
  vi.useFakeTimers();
  useSoundingStore.setState(useSoundingStore.getInitialState(), true);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("BackendProvider", () => {
  it.each(["event", "poll"])("retires process-local soundings on a new bridge instance through %s", async (mode) => {
    const replacement = { ...healthOk, bridge: { ...healthOk.bridge, pid: healthOk.bridge.pid + (mode === "event" ? 1 : 0), restarts: 1 } };
    const fake = controlledTransport(mode === "event" ? [healthOk, healthOk, replacement] : [healthOk, replacement]);
    if (mode === "poll") vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { queryClient } = setup(fake.transport);
    await act(async () => {
      if (mode === "event") fake.resolveListen();
      else fake.rejectListen("events unavailable");
      await vi.advanceTimersByTimeAsync(10);
    });
    await advance(10);
    const summary = parseSoundingSummary(summaryExample);
    useSoundingStore.getState().add(summary);
    useSoundingStore.getState().setView(summary.soundingId, INITIAL_VIEW);
    useSoundingStore.getState().setView(summary.soundingId, { ...INITIAL_VIEW, options: { chart: "winter" } });
    useSoundingStore.getState().close(summary.soundingId);
    expect(useSoundingStore.getState().pendingChanges).toHaveLength(1);
    useSoundingStore.getState().setSource("file");
    queryClient.setQueryData(["soundings", summary.soundingId, "analysis"], { cached: true });
    queryClient.setQueryData(QUERY_KEYS.catalog, { keep: true });
    let finishImage: (value: string) => void = () => undefined;
    const pending = queryClient.query({
      queryKey: ["soundings", summary.soundingId, "image", "MU"],
      queryFn: () => new Promise<string>((resolve) => { finishImage = resolve; }),
    }).catch(() => undefined);
    await advance(10);
    if (mode === "event") {
      await act(async () => { fake.emit(healthOk.bridge); await vi.advanceTimersByTimeAsync(10); });
      await advance(10);
      expect(useSoundingStore.getState().activeId).toBe(summary.soundingId);
      await act(async () => { fake.emit(replacement.bridge); await vi.advanceTimersByTimeAsync(10); });
    } else {
      await advance(5000);
    }
    await advance(10);
    expect(useSoundingStore.getState()).toMatchObject({ open: [], activeId: null, views: {}, pendingChanges: [], source: "file" });
    expect(queryClient.getQueryData(QUERY_KEYS.catalog)).toEqual({ keep: true });
    expect(queryClient.getQueryCache().findAll({ queryKey: ["soundings"] })).toHaveLength(0);
    await act(async () => { finishImage("old process image"); await pending; });
    expect(queryClient.getQueryCache().findAll({ queryKey: ["soundings"] })).toHaveLength(0);
  });

  it("queries health only after the subscription resolves, and recovers a lost event through the 1 s backstop", async () => {
    const fake = controlledTransport([healthWith(STARTING), healthOk]);
    setup(fake.transport);
    await advance(50);
    expect(fake.healthCalls()).toBe(0);
    // The bridge comes up before listen resolves: the event is lost.
    fake.emit(healthOk.bridge);
    await act(async () => {
      fake.resolveListen();
      await vi.advanceTimersByTimeAsync(10);
    });
    await advance(10);
    expect(fake.healthCalls()).toBe(1);
    expect(bridgeText()).toBe("starting");
    await advance(1000);
    expect(fake.healthCalls()).toBe(2);
    expect(bridgeText()).toBe("up");
    // Polling stops once the state settles.
    await advance(10_000);
    expect(fake.healthCalls()).toBe(2);
  });

  it("patches the cached health on an event and refetches it", async () => {
    const restarting = { status: "restarting", attempt: 1, retryInMs: 500, lastError: { code: "BRIDGE_CRASHED", message: "x", retryable: true } };
    const fake = controlledTransport([healthOk, healthWith(restarting)]);
    const { queryClient } = setup(fake.transport);
    await act(async () => {
      fake.resolveListen();
      await vi.advanceTimersByTimeAsync(10);
    });
    await advance(10);
    expect(bridgeText()).toBe("up");
    await act(async () => {
      fake.emit(restarting);
      await vi.advanceTimersByTimeAsync(10);
    });
    await advance(10);
    expect(fake.healthCalls()).toBe(2);
    expect(bridgeText()).toBe("restarting");
    expect(queryClient.getQueryState(QUERY_KEYS.health)?.status).toBe("success");
  });

  it("refetches a failed catalogue when the bridge returns to up", async () => {
    const fake = controlledTransport([healthOk]);
    const { queryClient } = setup(fake.transport);
    await act(async () => {
      fake.resolveListen();
      await vi.advanceTimersByTimeAsync(10);
    });
    await advance(10);
    const catalogFn = vi.fn(() => Promise.reject(new Error("still down")));
    await act(async () => {
      await queryClient.query({ queryKey: QUERY_KEYS.catalog, queryFn: catalogFn, retry: false }).catch(() => undefined);
    });
    expect(queryClient.getQueryState(QUERY_KEYS.catalog)?.status).toBe("error");
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await act(async () => {
      fake.emit(healthOk.bridge);
      await vi.advanceTimersByTimeAsync(10);
    });
    await advance(10);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: QUERY_KEYS.catalog });
  });

  it("warns once and polls every 5 s when listen rejects", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const fake = controlledTransport([healthOk]);
    setup(fake.transport);
    await act(async () => {
      fake.rejectListen("event.listen not allowed");
      await vi.advanceTimersByTimeAsync(10);
    });
    await advance(10);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(fake.healthCalls()).toBe(1);
    expect(bridgeText()).toBe("up");
    await advance(4900);
    expect(fake.healthCalls()).toBe(1);
    await advance(200);
    expect(fake.healthCalls()).toBe(2);
    await advance(5000);
    expect(fake.healthCalls()).toBe(3);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("unlistens on unmount", async () => {
    const fake = controlledTransport([healthOk]);
    const { view } = setup(fake.transport);
    await act(async () => {
      fake.resolveListen();
      await vi.advanceTimersByTimeAsync(10);
    });
    await advance(10);
    expect(fake.unlisten).not.toHaveBeenCalled();
    view.unmount();
    expect(fake.unlisten).toHaveBeenCalledTimes(1);
  });

  it("releases the first subscription of a development double mount", async () => {
    const releases: (() => void)[] = [];
    let listens = 0;
    const transport: Transport = {
      call: () => Promise.resolve(healthOk),
      listen: () => {
        listens += 1;
        const release = vi.fn();
        releases.push(release);
        return Promise.resolve(release);
      },
    };
    const { view } = setup(transport, true);
    await advance(10);
    await advance(10);
    expect(listens).toBe(2);
    expect(releases[0]).toHaveBeenCalledTimes(1);
    expect(releases[1]).not.toHaveBeenCalled();
    expect(bridgeText()).toBe("up");
    view.unmount();
    expect(releases[1]).toHaveBeenCalledTimes(1);
  });
});

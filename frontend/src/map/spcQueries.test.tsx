// @vitest-environment jsdom
import outlook from "@contracts/public/map/outlook.cat.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient } from "../lib/backend/queryOptions";
import type { Transport } from "../lib/backend/transport";
import { useAppStore } from "../stores/appStore";
import { scriptedTransport } from "../test/backendHarness";
import { useSpcOutlooks } from "./mapQueries";

const savedApp = useAppStore.getState();
beforeEach(() => { useAppStore.setState({ activeWorkspace: "map" }); });
afterEach(() => { cleanup(); useAppStore.setState(savedApp); vi.useRealTimers(); vi.restoreAllMocks(); });

function setup(transport: Transport) {
  const client = new BackendClient(transport);
  const queryClient = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  return { queryClient, wrapper };
}

it("resolves SPC issuances for the selected hour even when a cached outlook still covers it", async () => {
  const earlier = "2026-10-04T18:00:00Z", later = "2026-10-05T00:00:00Z";
  let resolveLater: (value: unknown) => void = () => undefined;
  const pending = new Promise((resolve) => { resolveLater = resolve; });
  const transport = scriptedTransport({ get_spc_outlook: [{ ...outlook, validTime: earlier }, pending] });
  const call = vi.spyOn(transport, "call");
  const { queryClient, wrapper } = setup(transport);
  const hook = renderHook(({ time }) => ({
    panel: useSpcOutlooks([true, false, false, false], time)[0],
    status: useSpcOutlooks([true, false, false, false], time)[0],
  }), { initialProps: { time: earlier }, wrapper });
  try {
    await waitFor(() => { expect(hook.result.current.panel?.isSuccess).toBe(true); });
    hook.rerender({ time: later });
    expect(hook.result.current.panel?.data).toBeUndefined();
    expect(hook.result.current.status?.data).toBeUndefined();
    await waitFor(() => { expect(call).toHaveBeenNthCalledWith(2, "get_spc_outlook", { product: "cat", validTime: later }); });
    await act(async () => {
      resolveLater({ ...outlook, validTime: later, outlook: { ...outlook.outlook, issued: "2026-10-04T20:00:00Z" } });
      await Promise.resolve();
    });
    await waitFor(() => { expect(hook.result.current.panel?.data?.outlook?.issued).toBe("2026-10-04T20:00:00Z"); });
    expect(hook.result.current.status?.data).toBe(hook.result.current.panel?.data);
    hook.rerender({ time: earlier });
    expect(hook.result.current.panel?.data?.validTime).toBe(earlier);
    expect(call).toHaveBeenCalledTimes(2);
  } finally { hook.unmount(); queryClient.clear(); }
});

it.each(["2026-10-03T23:58:00Z", "2026-10-04T11:58:00Z", "2026-10-04T23:58:00Z"])("refreshes SPC at startup and every five minutes across a clock boundary from %s", async (now) => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(now));
  const time = "2026-10-04T21:00:00Z";
  const transport = scriptedTransport({ get_spc_outlook: [{ ...outlook, outlook: null }, outlook] });
  const call = vi.spyOn(transport, "call");
  const { queryClient, wrapper } = setup(transport);
  const hook = renderHook(({ enabled, selectedTime }) => useSpcOutlooks([enabled, false, false, false], selectedTime)[0],
    { initialProps: { enabled: true, selectedTime: null as string | null }, wrapper });
  try {
    expect(call).not.toHaveBeenCalled();
    hook.rerender({ enabled: true, selectedTime: time });
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(hook.result.current?.data?.outlook).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60_000); });
    expect(call).toHaveBeenCalledTimes(2);
    expect(call).toHaveBeenLastCalledWith("get_spc_outlook", { product: "cat", validTime: time });
    expect(hook.result.current?.data?.outlook?.issued).toBe(outlook.outlook.issued);
    hook.rerender({ enabled: false, selectedTime: time });
    await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60_000); });
    expect(call).toHaveBeenCalledTimes(2);
  } finally { hook.unmount(); queryClient.clear(); }
});

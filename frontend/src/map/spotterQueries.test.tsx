// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient } from "../lib/backend/queryOptions";
import type { Transport } from "../lib/backend/transport";
import { useAppStore } from "../stores/appStore";
import { liveTimestamp, useLiveTrackingStore } from "../stores/liveTrackingStore";
import { useSpotterPositions } from "./spotterQueries";

const savedApp = useAppStore.getState();
const savedLive = useLiveTrackingStore.getState();
afterEach(() => {
  cleanup();
  useAppStore.setState(savedApp);
  useLiveTrackingStore.setState(savedLive);
  vi.useRealTimers();
});

it("shares polling, expires cached positions, and pauses when showing history or leaving Live Tracking", async () => {
  const now = Date.parse("2026-11-20T08:00:00Z");
  vi.useFakeTimers();
  vi.setSystemTime(now);
  useAppStore.setState({ activeWorkspace: "live" });
  useLiveTrackingStore.setState({ now, selectedTime: null, playing: false });
  const point = { latitude: 35, longitude: -97, updatedAt: liveTimestamp(now - 590_000), chaseCam: true };
  const call = vi.fn<Transport["call"]>().mockResolvedValueOnce({
    checkedAt: liveTimestamp(now), streamersAvailable: true, positions: [point],
  }).mockRejectedValue({ code: "OVERLAY_FETCH_FAILED", message: "Provider unavailable", retryable: true });
  const client = new BackendClient({ call, listen: () => Promise.resolve(() => undefined) });
  const queryClient = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const advance = async (ms: number) => { await act(() => vi.advanceTimersByTimeAsync(ms)); };
  const hook = renderHook(({ visible }) => [useSpotterPositions(visible), useSpotterPositions(visible)], {
    wrapper, initialProps: { visible: false },
  });
  try {
    expect(call).not.toHaveBeenCalled();
    hook.rerender({ visible: true });
    await advance(2);
    expect(call.mock.calls).toEqual([["get_spotter_positions", undefined]]);
    expect(hook.result.current.map((result) => result.positions)).toEqual([[point], [point]]);
    await advance(10_000);
    expect(hook.result.current.map((result) => result.positions)).toEqual([[], []]);
    expect(call).toHaveBeenCalledTimes(1);
    await advance(50_000);
    expect(call).toHaveBeenCalledTimes(2);

    act(() => { useLiveTrackingStore.getState().selectTime(now - 60_000); });
    await advance(120_000);
    expect(call).toHaveBeenCalledTimes(2);
    expect(hook.result.current.map((result) => result.positions)).toEqual([null, null]);
    act(() => {
      useAppStore.setState({ activeWorkspace: "overview" });
      useLiveTrackingStore.getState().selectTime(null);
    });
    await advance(120_000);
    expect(call).toHaveBeenCalledTimes(2);
  } finally {
    hook.unmount();
    queryClient.clear();
  }
});

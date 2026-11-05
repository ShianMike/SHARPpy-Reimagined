// @vitest-environment jsdom
import viewExample from "@contracts/public/soundings/view.example.json";
import summaryExample from "@contracts/public/soundings/summary.example.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { BackendProvider } from "../../lib/backend/BackendProvider";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { createQueryClient } from "../../lib/backend/queryOptions";
import { parseSoundingSummary } from "../../lib/contracts/soundings";
import { useSoundingStore } from "../../stores/soundingStore";
import { useSoundingView } from "./useSoundingView";
import { INITIAL_VIEW } from "./viewHistory";

afterEach(() => { cleanup(); useSoundingStore.setState(useSoundingStore.getInitialState(), true); });

it.each([false, true])("retains pending edit ownership after remount and ignores a replaced view (%s)", async (replaceView) => {
  const store = useSoundingStore.getState();
  store.add({ ...parseSoundingSummary(summaryExample), soundingId: "profile" });
  const cache = createQueryClient(), base = createTransport();
  const response = { ...viewExample, width: 3260, height: 2198, image: btoa(String.fromCharCode(137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13)) };
  let finish: (value: unknown) => void = () => undefined;
  const client = new BackendClient({ ...base, call: (command, args) => command === "render_sounding_view"
    ? (args?.view as { edits: unknown[] }).edits.length > 0 ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve(response)
    : base.call(command, args) });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={cache}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const first = renderHook(() => useSoundingView("profile", "MU", "uhd"), { wrapper });
  await waitFor(() => { expect(first.result.current.image.isSuccess).toBe(true); });
  act(() => { first.result.current.edit.mutate({ edits: [{ index: 0, values: { tmpc: 35 } }] }); });
  await waitFor(() => { expect(cache.isMutating()).toBe(1); });
  first.unmount();
  const next = renderHook(() => useSoundingView("profile", "MU", "uhd"), { wrapper });
  await waitFor(() => { expect(next.result.current.image.isSuccess).toBe(true); });
  expect(next.result.current.busy).toBe(true);
  if (replaceView) act(() => { store.setView("profile", { ...INITIAL_VIEW, options: { chart: "srw" } }); });
  act(() => { finish({ ...response, edits: [{ index: 0, values: { tmpc: 35 } }] }); });
  await waitFor(() => { expect(cache.isMutating()).toBe(0); });
  const view = useSoundingStore.getState().views.profile;
  if (replaceView) expect(view).toEqual({ ...INITIAL_VIEW, options: { chart: "srw" } });
  else expect(view?.history.at(-1)).toEqual([{ index: 0, values: { tmpc: 35 } }]);
  next.unmount(); cache.clear();
});

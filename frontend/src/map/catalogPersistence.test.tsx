// @vitest-environment jsdom
import catalog from "@contracts/public/map/catalog.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createTransport } from "../lib/backend/fixtureTransport";
import { createQueryClient } from "../lib/backend/queryOptions";
import { parseMapCatalog } from "../lib/contracts/map";
import { useForecastStore } from "../stores/forecastStore";
import { useMapCatalog } from "./catalogQuery";

afterEach(() => { cleanup(); useForecastStore.setState(useForecastStore.getInitialState(), true); vi.restoreAllMocks(); });

it("retains the palette while another hour loads, then publishes its real inventory", async () => {
  const client = new BackendClient(createTransport()), queryClient = createQueryClient();
  useForecastStore.setState({ modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 1 });
  let finish!: (value: ReturnType<typeof parseMapCatalog>) => void;
  const get = vi.spyOn(client, "getMapCatalog");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const hook = renderHook(useMapCatalog, { wrapper });
  await waitFor(() => { expect(hook.result.current.isSuccess).toBe(true); });
  const palette = hook.result.current.data;
  get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  act(() => { useForecastStore.getState().setHour(2); });
  expect(hook.result.current.isPlaceholderData).toBe(true);
  expect(hook.result.current.data).toBe(palette);
  await waitFor(() => { expect(get).toHaveBeenLastCalledWith("hrrr", "2026-10-04T12:00:00Z", 2); });
  const updated = { ...parseMapCatalog(catalog), products: [] };
  act(() => { finish(updated); });
  await waitFor(() => { expect(hook.result.current.isPlaceholderData).toBe(false); });
  expect(hook.result.current.data?.products).toEqual([]);
  hook.unmount(); queryClient.clear();
});

it.each(["model", "run"])("does not carry palettes across a different %s", async (kind) => {
  const client = new BackendClient(createTransport()), queryClient = createQueryClient();
  useForecastStore.setState({ modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 1 });
  const get = vi.spyOn(client, "getMapCatalog");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>;
  const hook = renderHook(useMapCatalog, { wrapper });
  await waitFor(() => { expect(hook.result.current.data).toBeDefined(); });
  get.mockImplementation(() => new Promise(() => undefined));
  act(() => { if (kind === "model") useForecastStore.getState().setModel("gfs"); else useForecastStore.getState().setRun("2026-10-04T06:00:00Z"); });
  expect(hook.result.current.data).toBeUndefined();
  hook.unmount(); queryClient.clear();
});

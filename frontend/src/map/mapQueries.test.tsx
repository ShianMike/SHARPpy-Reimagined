// @vitest-environment jsdom
import field from "@contracts/public/map/field.shrf?url&inline";
import radar from "@contracts/public/map/radar.shrf?url&inline";
import radarSite from "@contracts/public/map/radar-site.shrf?url&inline";
import satellite from "@contracts/public/map/satellite.shrf?url&inline";
import outlook from "@contracts/public/map/outlook.cat.json";
import reports from "@contracts/public/map/reports.json";
import surface from "@contracts/public/map/surface.json";
import catalog from "@contracts/public/map/catalog.json";
import modelCatalogs from "@contracts/public/map/catalog.models.json";
import contours from "@contracts/public/map/contours.json";
import models from "@contracts/public/soundings/models.catalog.json";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BackendProvider } from "../lib/backend/BackendProvider";
import { BackendClient } from "../lib/backend/client";
import { createQueryClient, mapKeys, QUERY_KEYS } from "../lib/backend/queryOptions";
import { parseFieldFrame } from "../lib/contracts/frames";
import { parseMapCatalog } from "../lib/contracts/map";
import { parseModelCatalog } from "../lib/contracts/soundings";
import type { Transport } from "../lib/backend/transport";
import { useFieldFrame, useRadarFrame, useRadarSiteFrame, useSatelliteFrame, useSpcOutlooks, useStormReports } from "./mapQueries";
import { useSurfacePlots } from "./surfaceQueries";
import { useFieldContours } from "./contourQueries";
import { defaultLayerStates } from "./layers/registry";
import { defaultProductFor } from "./panelField";
import { usePlayback } from "./usePlayback";
import { useMapStore } from "../stores/mapStore";
import { useForecastStore } from "../stores/forecastStore";
import { useGraphicsStore } from "../stores/graphicsStore";
import type { FieldFrame } from "../types/map";

const bytes = (url: string): ArrayBuffer => Uint8Array.from(atob(url.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer;
const payloads = { field: bytes(field), radar: bytes(radar), radarSite: bytes(radarSite), satellite: bytes(satellite), outlook, reports, surface };
type Kind = keyof typeof payloads;

function useLayerQuery(kind: Kind, changed: boolean) {
  const time = changed ? "2026-10-09T21:00:00Z" : "2026-10-04T21:00:00Z";
  const fieldQuery = useFieldFrame(kind === "field" ? { model: "hrrr", product: "refc", runTime: time, forecastHour: 1 } : null);
  const radarQuery = useRadarFrame(changed ? "base" : null, kind === "radar");
  const siteQuery = useRadarSiteFrame(null, changed ? "KFDR" : "KTLX", kind === "radarSite");
  const satelliteQuery = useSatelliteFrame("infrared", time, kind === "satellite");
  const outlookQuery = useSpcOutlooks([kind === "outlook", false, false, false], time)[0];
  const reportQuery = useStormReports(time, 21600, "all", kind === "reports");
  const surfaceQuery = useSurfacePlots({ latitude: changed ? 40 : 35.2, longitude: -97.4 }, time, kind === "surface");
  return { field: fieldQuery, radar: radarQuery, radarSite: siteQuery, satellite: satelliteQuery, outlook: outlookQuery, reports: reportQuery, surface: surfaceQuery }[kind];
}

const savedMap = useMapStore.getState();
const savedForecast = useForecastStore.getState();
const savedGraphics = useGraphicsStore.getState();
afterEach(() => {
  cleanup(); useMapStore.setState(savedMap); useForecastStore.setState(savedForecast); useGraphicsStore.setState(savedGraphics);
});

it.each(Object.keys(payloads) as Kind[])("clears %s data when its selection changes", async (kind) => {
  let calls = 0;
  const transport: Transport = {
    call: vi.fn(() => {
      calls += 1;
      return calls === 1 ? Promise.resolve(payloads[kind]) : new Promise<unknown>(() => undefined);
    }),
    listen: () => Promise.resolve(() => undefined),
  };
  const client = new BackendClient(transport);
  const queryClient = createQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(({ changed }) => useLayerQuery(kind, changed), { initialProps: { changed: false }, wrapper });
  try {
    await waitFor(() => { expect(hook.result.current?.isSuccess).toBe(true); });
    expect(hook.result.current?.data).toBeDefined();
    hook.rerender({ changed: true });
    expect(hook.result.current?.data).toBeUndefined();
    expect(hook.result.current?.isPlaceholderData).toBe(false);
    await waitFor(() => { expect(calls).toBe(2); });
  } finally {
    hook.unmount();
    queryClient.clear();
  }
});

it.each(["rrfs-a", "nam-3km-conus", "refs-m1", "refs-m2", "refs-m3", "refs-m4", "refs-m5"])(
  "keeps %s field and wind/contour requests separate from HRRR at the same run and hour", async (model) => {
    const call = vi.fn((command: string, args?: Readonly<Record<string, unknown>>) =>
      args?.model === "hrrr" ? Promise.resolve(command === "get_weather_field" ? payloads.field : contours)
        : new Promise<unknown>(() => undefined));
    const client = new BackendClient({ call, listen: () => Promise.resolve(() => undefined) });
    const queryClient = createQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
    );
    const runTime = "2026-10-04T18:00:00Z";
    const hook = renderHook(({ selected }) => ({
      field: useFieldFrame({ model: selected, product: "hgt-wind-500", runTime, forecastHour: 1 }),
      contours: useFieldContours("hgt-wind-500", runTime, 1, true, selected),
    }), { initialProps: { selected: "hrrr" }, wrapper });
    try {
      await waitFor(() => { expect(hook.result.current.field.isSuccess && hook.result.current.contours.isSuccess).toBe(true); });
      hook.rerender({ selected: model });
      expect(hook.result.current.field.data).toBeUndefined();
      expect(hook.result.current.contours.data).toBeUndefined();
      await waitFor(() => { expect(call).toHaveBeenCalledTimes(4); });
      for (const command of ["get_weather_field", "get_field_contours"]) {
        expect(call).toHaveBeenCalledWith(command, { model, product: "hgt-wind-500", runTime, forecastHour: 1 });
      }
      const catalogue = parseMapCatalog((modelCatalogs as Readonly<Record<string, unknown>>)[model]);
      expect(catalogue.fieldModel).toBe(model);
      expect(catalogue.products).toHaveLength(model.startsWith("refs-") ? 16 : model === "nam-3km-conus" ? 24 : 25);
      expect(catalogue.products.some(({ key }) => key === "ltng")).toBe(model !== "nam-3km-conus");
      const secondaryProduct = defaultProductFor(parseMapCatalog(catalog), 1)?.key;
      expect(catalogue.products.some(({ key }) => key === secondaryProduct)).toBe(!model.startsWith("refs-"));
    } finally { hook.unmount(); queryClient.clear(); }
  },
);

it.each([2, 4] as const)("prefetches %i panels concurrently, with only one future hour in flight", async (panelLayout) => {
  const mapCatalog = parseMapCatalog(catalog);
  const frame = parseFieldFrame(payloads.field);
  const runTime = "2026-10-04T12:00:00Z";
  const products = Array.from({ length: panelLayout }, (_, panel) => {
    const product = defaultProductFor(mapCatalog, panel);
    if (product === undefined) throw new Error("Fixture panel product missing");
    return product.key;
  });
  const layers = defaultLayerStates(); layers["model-field"].visible = true;
  useMapStore.setState({ panelLayout, productKey: null, layers, panels: Array.from({ length: 3 }, () => ({ productKey: null, layers })) });
  useForecastStore.setState({ modelKey: "hrrr", runTime, forecastHour: 0, playing: false });
  useGraphicsStore.setState({ effective: { ...savedGraphics.effective, profile: "high" } });
  const client = new BackendClient({ call: () => Promise.reject(new Error("Unexpected request")), listen: () => Promise.resolve(() => undefined) });
  const pending: { product: string; hour: number; resolve: (frame: FieldFrame) => void }[] = [];
  vi.spyOn(client, "getWeatherField").mockImplementation((product, _run, hour) => new Promise((resolve) => { pending.push({ product, hour, resolve }); }));
  const queryClient = createQueryClient();
  queryClient.setQueryData(QUERY_KEYS.models, parseModelCatalog(models));
  queryClient.setQueryData(mapKeys.catalog("hrrr"), mapCatalog);
  products.forEach((product) => { queryClient.setQueryData(mapKeys.field(product, runTime, 0), frame); });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}><BackendProvider client={client}>{children}</BackendProvider></QueryClientProvider>
  );
  const hook = renderHook(usePlayback, { wrapper });
  try {
    await waitFor(() => { expect(pending.map(({ product, hour }) => [product, hour])).toEqual(products.map((product) => [product, 1])); });
    await act(async () => {
      pending.slice(0, -1).forEach(({ resolve }) => { resolve(frame); });
      await Promise.resolve();
    });
    expect(pending).toHaveLength(panelLayout);
    await act(async () => { pending[panelLayout - 1]?.resolve(frame); await Promise.resolve(); });
    await waitFor(() => { expect(pending.slice(panelLayout).map(({ product, hour }) => [product, hour])).toEqual(products.map((product) => [product, 2])); });
    hook.unmount();
    await act(async () => {
      pending.slice(panelLayout).forEach(({ resolve }) => { resolve(frame); });
      await Promise.resolve();
    });
    expect(pending).toHaveLength(2 * panelLayout);
  } finally { hook.unmount(); queryClient.clear(); }
});

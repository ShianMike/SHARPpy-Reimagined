// @vitest-environment jsdom
import radar from "@contracts/public/map/radar.shrf?url&inline";
import alerts from "@contracts/public/map/alerts.json";
import outlook from "@contracts/public/map/outlook.cat.json";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import { LayerPanel } from "./LayerPanel";

const map = useMapStore.getInitialState(), app = useAppStore.getInitialState();
const clock = useLiveTrackingStore.getInitialState(), forecast = useForecastStore.getInitialState();
const time = Date.parse("2026-10-04T21:00:00Z");
beforeEach(() => {
  useMapStore.setState(map, true); useAppStore.setState(app, true);
  useLiveTrackingStore.setState({ ...clock, now: time }, true); useForecastStore.setState(forecast, true);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("collects only enabled layers, deduplicates provider keys, and follows opacity and order changes", async () => {
  useAppStore.getState().setActiveWorkspace("live");
  const store = useMapStore.getState();
  store.setLayerVisible("warnings", true); store.setLayerVisible("watches", true);
  const transport = createTransport(), original = transport.call.bind(transport);
  const calls = vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_weather_alerts"
    ? Promise.resolve({ ...structuredClone(alerts), alerts: [...alerts.alerts, { ...alerts.alerts[0], id: "duplicate-warning" }] }) : command === "get_radar_frame"
    ? Promise.resolve(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer)
    : original(command, args));
  const { view, queryClient } = renderWithBackend(<LayerPanel availabilities={new Set()} />, transport);
  fireEvent.click(screen.getByRole("button", { name: "Legend" }));
  const legend = within(screen.getByRole("region", { name: "Map legend" }));
  await waitFor(() => { expect(legend.getByText("Tornado Warning")).toBeTruthy(); });
  expect(legend.getAllByText("Tornado Warning")).toHaveLength(1);
  expect(legend.getByText("2 active warnings")).toBeTruthy();
  expect(legend.getByText("Tornado Watch")).toBeTruthy();
  expect(legend.getByTestId("legend-warnings").querySelector('[style*="--key-stroke"]')?.getAttribute("style")).toContain("#ff0000");
  expect(legend.getAllByTestId(/^legend-/u).map((el) => el.dataset.testid)).toEqual(["legend-warnings", "legend-watches", "legend-radar"]);
  expect(legend.queryByTestId("legend-satellite")).toBeNull();
  expect(calls.mock.calls.some(([command]) => command === "get_satellite_frame")).toBe(false);
  act(() => { store.setLayerOpacity("radar", 0); store.moveLayer("radar", 1); });
  expect(legend.getAllByTestId(/^legend-/u).map((el) => el.dataset.testid)).toEqual(["legend-warnings", "legend-radar", "legend-watches"]);
  expect(within(legend.getByTestId("legend-radar")).getByText("Fully transparent on the map.")).toBeTruthy();
  expect(legend.getByRole("list", { name: "Scale ticks, dBZ" })).toBeTruthy();
  act(() => { store.setLayerVisible("warnings", false); });
  expect(legend.queryByText("Tornado Warning")).toBeNull();
  expect(legend.getByText("Tornado Watch")).toBeTruthy();
  view.unmount(); queryClient.clear();
});

it("hides the active panel's weather together while retaining opacity, order, map details and other panels", () => {
  useAppStore.getState().setActiveWorkspace("live");
  const store = useMapStore.getState();
  store.setPanelLayout(2); store.setActivePanel(1);
  store.setLayerVisible("warnings", true); store.setLayerVisible("satellite", true); store.setLayerOpacity("radar", 0.35);
  const other = panelLayers(useMapStore.getState(), 0), order = useMapStore.getState().layerOrder;
  const { view, queryClient } = renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport());
  fireEvent.click(screen.getByRole("button", { name: "Hide all weather layers" }));
  const layers = panelLayers(useMapStore.getState(), 1);
  expect([layers.radar.visible, layers.warnings.visible, layers.satellite.visible]).toEqual([false, false, false]);
  expect(layers.radar.opacity).toBe(0.35);
  expect(layers.basemap.visible).toBe(true);
  expect(layers["radar-site-labels"].visible).toBe(true);
  expect(panelLayers(useMapStore.getState(), 0)).toBe(other);
  expect(useMapStore.getState().layerOrder).toBe(order);
  fireEvent.click(screen.getByRole("button", { name: "Legend" }));
  expect(screen.getByText("No weather layers enabled. Add one in Weather to see its key.")).toBeTruthy();
  view.unmount(); queryClient.clear();
});

it("uses SPC's actual colors and omits an outlook that does not cover the selected time", async () => {
  useAppStore.getState().setActiveWorkspace("map");
  useForecastStore.setState({ modelKey: "hrrr", runTime: "2026-10-04T12:00:00Z", forecastHour: 9 });
  useMapStore.getState().setLayerVisible("spc-cat", true);
  useMapStore.getState().setLayerPanelGroup("legend");
  let covers = true;
  const transport = createTransport(), original = transport.call.bind(transport);
  vi.spyOn(transport, "call").mockImplementation((command, args) => command === "get_spc_outlook"
    ? Promise.resolve({ ...structuredClone(outlook), validTime: args?.validTime, outlook: { ...structuredClone(outlook.outlook), covers } }) : original(command, args));
  const { view, queryClient } = renderWithBackend(<LayerPanel availabilities={new Set()} />, transport);
  const key = within(screen.getByTestId("legend-spc-cat"));
  await waitFor(() => { expect(key.getByText("TSTM")).toBeTruthy(); });
  expect(key.getByText("TSTM").previousElementSibling?.getAttribute("style")).toContain("#C1E9C1");
  await act(async () => { covers = false; await queryClient.invalidateQueries({ queryKey: ["outlooks"] }); });
  await waitFor(() => { expect(key.getByText("No SPC outlook covers this time.")).toBeTruthy(); });
  expect(key.queryByText("TSTM")).toBeNull();
  view.unmount(); queryClient.clear();
});

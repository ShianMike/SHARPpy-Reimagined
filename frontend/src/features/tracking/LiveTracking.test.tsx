// @vitest-environment jsdom
import radar from "@contracts/public/map/radar.shrf?url&inline";
import correlationPalette from "@contracts/public/map/radar-correlation.palette.json";
import echoTopsPalette from "@contracts/public/map/radar-echo-tops.palette.json";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackendClient } from "../../lib/backend/client";
import { encodeShrf, parseShrf } from "../../lib/contracts/frames";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { mapKeys } from "../../lib/backend/queryOptions";
import { LayerPanel } from "../../map/controls/LayerPanel";
import { useRadarSiteFrame } from "../../map/mapQueries";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { LIVE_MINUTE, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { applyPreferences, currentPreferences, parsePreferences, resetLayerSettings } from "../../stores/preferences";
import { renderWithBackend } from "../../test/backendHarness";
import { LiveTimeline } from "./LiveTimeline";
import { LiveRadarControls } from "./LiveRadarControls";
import { RadarScale } from "./RadarScale";
import { isLiveQuery, liveRequests } from "./liveRequests";

const map = useMapStore.getState(), app = useAppStore.getState(), forecast = useForecastStore.getState();
const clock = useLiveTrackingStore.getState();
const ui = useUiStore.getState();
const now = Date.parse("2026-10-17T08:00:00Z");
function transportWithRadar() {
  const transport = createTransport();
  const call = transport.call.bind(transport);
  transport.call = (command, args) => command === "get_radar_frame"
    ? Promise.resolve(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer)
    : call(command, args);
  return transport;
}
beforeEach(() => {
  useMapStore.setState(map, true); useAppStore.setState(app, true); useForecastStore.setState(forecast, true);
  useLiveTrackingStore.setState({ ...clock, now, selectedTime: null }, true);
  useUiStore.setState(ui, true);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it("offers Correlation Coefficient with checked tilts and Enhanced Echo Tops with its own full-volume scale", async () => {
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite("KTLX");
  useMapStore.getState().setRadarTilt("1");
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  const transport = transportWithRadar();
  const call = transport.call.bind(transport);
  const decoded = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
  transport.call = (command, args) => command === "get_radar_site_frame"
    ? Promise.resolve(encodeShrf({ ...decoded.header, kind: "radar-site", siteId: "KTLX",
      product: args?.product ?? "reflectivity", elevationAngle: args?.product === "echo-tops" ? null : 1.3,
      label: args?.product === "correlation-coefficient" ? "Correlation Coefficient" : args?.product === "echo-tops" ? "Enhanced Echo Tops" : "Base Reflectivity",
      units: args?.product === "correlation-coefficient" ? "" : args?.product === "echo-tops" ? "kft" : "dBZ",
      palette: args?.product === "correlation-coefficient" ? correlationPalette : args?.product === "echo-tops" ? echoTopsPalette : decoded.header.palette,
    }, decoded.body)) : call(command, args);
  const { queryClient } = renderWithBackend(<><LiveRadarControls /><RadarScale panel={0} /></>, transport);
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Product" }).textContent).toContain("Base Reflectivity"); });
  fireEvent.click(screen.getByRole("combobox", { name: "Site Product" }));
  fireEvent.click(screen.getByRole("option", { name: "Correlation Coefficient" }));
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Tilt" }).textContent).toContain("1.3°"); });
  expect(useMapStore.getState().livePanels[0]).toMatchObject({ radarProduct: "correlation-coefficient", radarTilt: "1" });
  expect(screen.getByRole("slider", { name: "Explore parameter scale" }).getAttribute("aria-valuemax")).toBe("1.05");
  expect(screen.getByTestId("radar-scale").getAttribute("aria-description")).toContain("Correlation Coefficient · Unitless");
  fireEvent.click(screen.getByRole("combobox", { name: "Site Product" }));
  fireEvent.click(screen.getByRole("option", { name: "Enhanced Echo Tops" }));
  expect(screen.getByRole("button", { name: "Site Tilt" }).hasAttribute("disabled")).toBe(true);
  expect(useMapStore.getState().livePanels[0]).toMatchObject({ radarProduct: "echo-tops", radarTilt: "0" });
  await waitFor(() => { expect(screen.getByTestId("radar-scale").getAttribute("aria-description")).toContain("Enhanced Echo Tops · kft"); });
  const scale = screen.getByRole("slider", { name: "Explore parameter scale" });
  expect(scale.getAttribute("aria-valuemax")).toBe("80");
  fireEvent.keyDown(scale, { key: "End" });
  expect(scale.getAttribute("aria-valuetext")).toBe("80 kft");
  queryClient.clear();
});

it("keeps radar toolbar choices in the active live panel and reports the source's actual tilt support", async () => {
  useAppStore.getState().setActiveWorkspace("live");
  const { view, queryClient } = renderWithBackend(<LiveRadarControls />, createTransport());
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Product" }).textContent).toContain("Composite Reflectivity"); });
  expect(screen.getByRole("button", { name: "Site Tilt" }).textContent).toContain("All tilts");
  fireEvent.click(screen.getByRole("combobox", { name: "Site" }));
  fireEvent.click(await screen.findByRole("option", { name: "KTLX" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Site Product" }));
  fireEvent.click(screen.getByRole("option", { name: "Velocity" }));
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Tilt" }).textContent).toContain("0.5°"); });
  fireEvent.click(screen.getByRole("combobox", { name: "Site Tilt" }));
  expect(screen.queryByRole("option", { name: "−0.2°" })).toBeNull();
  expect(screen.queryByRole("option", { name: "0.0° / 0.2°" })).toBeNull();
  expect(screen.queryByRole("option", { name: "0.3° / 0.4°" })).toBeNull();
  expect(screen.queryByText("Requires an available scan at this elevation")).toBeNull();
  fireEvent.click(screen.getByRole("option", { name: "1.3° / 1.5°" }));
  const chosen = useMapStore.getState().livePanels[0];
  expect(chosen).toMatchObject({ radarScope: "site", radarSite: "KTLX", radarProduct: "velocity", radarTilt: "1" });
  act(() => { useMapStore.getState().setPanelLayout(2); useMapStore.getState().setActivePanel(1); });
  expect(screen.getByRole("combobox", { name: "Site" }).textContent).toContain("CONUS mosaic");
  expect(screen.getByRole("combobox", { name: "Site Product · 2" }).textContent).toContain("Composite Reflectivity");
  expect(screen.getByRole("button", { name: "Site Tilt" }).textContent).toContain("All tilts");
  expect(useMapStore.getState().livePanels[0]).toBe(chosen);
  expect(useMapStore.getState().panels).toBe(map.panels);
  act(() => { useMapStore.getState().setActivePanel(0); });
  expect(screen.getByRole("combobox", { name: "Site" }).textContent).toContain("KTLX");
  expect(screen.getByRole("combobox", { name: "Site Product" }).textContent).toContain("Velocity");
  expect(screen.getByRole("combobox", { name: "Site Tilt" }).textContent).toContain("1.3° / 1.5°");
  act(() => { useMapStore.getState().setActivePanel(0); useMapStore.getState().setRadarProduct("one-hour"); });
  expect(screen.getByRole("button", { name: "Site Tilt" }).textContent).toContain("Not applicable");
  view.unmount(); queryClient.clear();
});

it("toggles and restores radar site labels independently for each live panel", () => {
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setPanelLayout(2);
  useMapStore.getState().setLayerPanelGroup("map");
  const { view, queryClient } = renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport());
  const toggle = screen.getByRole("switch", { name: "Radar site labels" });
  expect(toggle.getAttribute("aria-checked")).toBe("true");
  fireEvent.click(toggle);
  expect(panelLayers(useMapStore.getState(), 0)["radar-site-labels"].visible).toBe(false);
  expect(panelLayers(useMapStore.getState(), 1)["radar-site-labels"].visible).toBe(true);
  const saved = parsePreferences(JSON.stringify(currentPreferences()));
  act(() => { resetLayerSettings(); applyPreferences(saved); });
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Panel 2" }));
  expect(screen.getByRole("switch", { name: "Radar site labels" }).getAttribute("aria-checked")).toBe("true");
  act(() => { useAppStore.getState().setActiveWorkspace("map"); });
  expect(screen.queryByRole("switch", { name: "Radar site labels" })).toBeNull();
  view.unmount(); queryClient.clear();
});

it("clamps history to the past hour, including system clock corrections, without changing the forecast", () => {
  const live = useLiveTrackingStore.getState();
  live.selectTime(now - 90 * LIVE_MINUTE);
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 60 * LIVE_MINUTE);
  live.tick(now + 10 * LIVE_MINUTE);
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 50 * LIVE_MINUTE);
  live.selectTime(now + 30 * LIVE_MINUTE);
  live.tick(now - 10 * LIVE_MINUTE);
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 10 * LIVE_MINUTE);
  live.selectTime(Number.NaN); live.tick(Number.POSITIVE_INFINITY);
  expect(useLiveTrackingStore.getState().now).toBe(now - 10 * LIVE_MINUTE);
  live.selectTime(null);
  expect(useLiveTrackingStore.getState().selectedTime).toBeNull();
  expect(useForecastStore.getState()).toEqual(forecast);
});

it("saves and resets live layers independently while preserving model panels and products", () => {
  useMapStore.getState().setPanelLayout(4);
  useMapStore.getState().setActivePanel(2);
  useMapStore.getState().setProduct("refc");
  const modelPanels = useMapStore.getState().panels;
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setActivePanel(0);
  useMapStore.getState().setLayerVisible("satellite", true);
  useMapStore.getState().setLayerOpacity("radar", 0.7);
  expect(panelLayers(useMapStore.getState(), 0).satellite.visible).toBe(true);
  expect(useMapStore.getState().panels).toBe(modelPanels);
  const saved = currentPreferences();
  resetLayerSettings();
  expect(useMapStore.getState().livePanels[0]?.layers.satellite.visible).toBe(false);
  expect(useMapStore.getState().panels).toBe(modelPanels);
  applyPreferences(saved);
  expect(useMapStore.getState().livePanels[0]?.layers.radar.opacity).toBe(0.7);
  useAppStore.getState().setActiveWorkspace("map");
  expect(useMapStore.getState()).toMatchObject({ panelLayout: 4, activePanel: 0, panels: modelPanels });
  expect(panelLayers(useMapStore.getState(), 0).satellite.visible).toBe(false);
});

it("addresses enabled live sources with the requested time and never requests a model field", async () => {
  const transport = transportWithRadar();
  const call = vi.spyOn(transport, "call");
  const time = new Date(now).toISOString();
  const requests = liveRequests(new BackendClient(transport), useMapStore.getState(), null, null, time);
  expect(requests.map((request) => request.queryKey)).toEqual([mapKeys.radar(null, time)]);
  await requests[0]?.queryFn();
  expect(call).toHaveBeenCalledWith("get_radar_frame", { validTime: time });
  expect(isLiveQuery(mapKeys.field("refc", time, 0))).toBe(false);
  expect(isLiveQuery(mapKeys.radar(null))).toBe(false);
  expect(isLiveQuery(requests[0]?.queryKey ?? [])).toBe(true);
});

it("loads each panel's own radar and satellite settings while sharing identical requests", () => {
  useAppStore.getState().setActiveWorkspace("live");
  const state = useMapStore.getState();
  state.setPanelLayout(4);
  state.setActivePanel(1); state.setRadarScope("site"); state.setRadarProduct("velocity"); state.setRadarSite("KTLX"); state.setRadarTilt("2");
  state.setActivePanel(2); state.setLayerVisible("radar", false); state.setLayerVisible("satellite", true);
  const saved = currentPreferences();
  const time = "2026-10-17T08:00:00Z";
  const requests = liveRequests(new BackendClient(transportWithRadar()), useMapStore.getState(), "KOUN", null, time);
  expect(requests.map((request) => request.queryKey)).toEqual([mapKeys.radar(null, time), mapKeys.radarSite("velocity", "KTLX", time, "2"), mapKeys.satellite("infrared", time)]);
  resetLayerSettings(); applyPreferences(saved);
  expect(useMapStore.getState().livePanels[1]).toMatchObject({ radarScope: "site", radarProduct: "velocity", radarSite: "KTLX", radarTilt: "2" });
  expect(useMapStore.getState().livePanels[0]?.radarScope).toBe("mosaic");
});

it("plays loaded history, pauses on stepping, and stops at the end without looping", async () => {
  vi.useFakeTimers(); vi.setSystemTime(now);
  useAppStore.getState().setActiveWorkspace("live");
  useUiStore.setState({ speed: 1, loop: false });
  const { view, queryClient } = renderWithBackend(<LiveTimeline />, transportWithRadar());
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 60 * LIVE_MINUTE);
  await act(async () => { await vi.advanceTimersByTimeAsync(751); });
  expect(useLiveTrackingStore.getState().selectedTime).toBe(now - 55 * LIVE_MINUTE);
  fireEvent.click(screen.getByRole("button", { name: "Next frame" }));
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: now - 50 * LIVE_MINUTE, playing: false });
  fireEvent.change(screen.getByRole("slider", { name: "Live history" }), { target: { value: "-5" } });
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(751); });
  expect(useLiveTrackingStore.getState()).toMatchObject({ selectedTime: now, playing: false });
  expect(useForecastStore.getState()).toEqual(forecast);
  view.unmount(); queryClient.clear();
});

it.each([0, 10])("keeps the same radar source while a recent request refreshes (%s minute provider lag)", async (delay) => {
  const transport = createTransport();
  const call = transport.call.bind(transport);
  const first = "2026-10-17T08:00:00Z";
  const scan = new Date(Date.parse(first) - delay * LIVE_MINUTE).toISOString().replace(".000Z", "Z");
  const frame = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
  transport.call = (command, args) => command !== "get_radar_site_frame" ? call(command, args)
    : args?.validTime !== first || args.site !== "KTLX" || args.product !== "velocity" ? new Promise(() => undefined)
    : Promise.resolve(encodeShrf({ ...frame.header, kind: "radar-site", siteId: "KTLX", product: "velocity", units: "mph", validTime: scan }, frame.body));
  useMapStore.setState({ radarSite: "KTLX", radarProduct: "velocity" });
  useLiveTrackingStore.setState({ now, selectedTime: now });
  function Probe() {
    const { radarProduct, radarSite, radarTilt } = useMapStore();
    const time = new Date(useLiveTrackingStore((state) => state.selectedTime ?? state.now)).toISOString().replace(".000Z", "Z");
    const frame = useRadarSiteFrame(radarProduct, radarSite, true, time, radarTilt);
    return <output data-testid="radar-probe">{frame.data?.validTime ?? "empty"}</output>;
  }
  const { view, queryClient } = renderWithBackend(<Probe />, transport);
  await waitFor(() => { expect(screen.getByTestId("radar-probe").textContent).toBe(scan); });
  act(() => { useLiveTrackingStore.setState({ selectedTime: now + LIVE_MINUTE }); });
  expect(screen.getByTestId("radar-probe").textContent).toBe(scan);
  act(() => { useMapStore.getState().setRadarTilt("1"); });
  expect(screen.getByTestId("radar-probe").textContent).toBe("empty");
  const cached = await new BackendClient(transport).getRadarSiteFrame({ site: "KTLX" }, "velocity", first, "0");
  queryClient.setQueryData(mapKeys.radarSite("velocity", "KTLX", first, "1"), { ...cached, elevationAngle: 1.3 });
  act(() => { useMapStore.getState().setRadarTilt("0"); });
  expect(screen.getByTestId("radar-probe").textContent).toBe(scan);
  act(() => { useMapStore.getState().setRadarTilt("1"); });
  expect(screen.getByTestId("radar-probe").textContent).toBe(scan);
  act(() => { useMapStore.getState().setRadarTilt("0"); });
  act(() => { useLiveTrackingStore.setState({ selectedTime: now + 11 * LIVE_MINUTE }); });
  expect(screen.getByTestId("radar-probe").textContent).toBe("empty");
  act(() => { useLiveTrackingStore.setState({ selectedTime: now - LIVE_MINUTE }); });
  expect(screen.getByTestId("radar-probe").textContent).toBe("empty");
  act(() => { useLiveTrackingStore.setState({ selectedTime: now + LIVE_MINUTE }); useMapStore.setState({ radarSite: "KOUN" }); });
  expect(screen.getByTestId("radar-probe").textContent).toBe("empty");
  act(() => { useMapStore.setState({ radarSite: "KTLX", radarProduct: "reflectivity" }); });
  expect(screen.getByTestId("radar-probe").textContent).toBe("empty");
  view.unmount(); queryClient.clear();
});

it("shares reports across map workspaces while keeping outlooks in Model Viewer and alerts in Live Tracking", async () => {
  const { view, queryClient } = renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport());
  expect(screen.queryByTestId("layer-row-radar")).toBeNull();
  expect(screen.getByTestId("layer-row-model-field")).toBeTruthy();
  for (const id of ["spc-cat", "spc-hail", "spc-wind", "spc-torn", "storm-reports"]) expect(screen.getByTestId(`layer-row-${id}`)).toBeTruthy();
  for (const id of ["warnings", "watches"]) expect(screen.queryByTestId(`layer-row-${id}`)).toBeNull();
  view.unmount();
  useAppStore.getState().setActiveWorkspace("live");
  const live = renderWithBackend(<LayerPanel availabilities={new Set()} />, transportWithRadar());
  expect(await screen.findByTestId("layer-row-radar")).toBeTruthy();
  expect(screen.queryByTestId("layer-row-model-field")).toBeNull();
  for (const id of ["spc-cat", "spc-hail", "spc-wind", "spc-torn"]) expect(screen.queryByTestId(`layer-row-${id}`)).toBeNull();
  for (const id of ["warnings", "watches", "storm-reports"]) expect(screen.getByTestId(`layer-row-${id}`)).toBeTruthy();
  expect(screen.queryByRole("group", { name: "Active map panel" })).toBeNull();
  live.view.unmount(); live.queryClient.clear(); queryClient.clear();
});

it("shares warning/watch and storm-report requests without fetching forecast outlooks", async () => {
  useAppStore.getState().setActiveWorkspace("live");
  for (const id of ["radar", "spc-cat", "storm-reports", "warnings", "watches"] as const) useMapStore.getState().setLayerVisible(id, id !== "radar");
  const transport = createTransport();
  const call = vi.spyOn(transport, "call");
  const requests = liveRequests(new BackendClient(transport), useMapStore.getState(), null, null, "2026-10-04T21:00:00Z");
  expect(requests.map(({ queryKey }) => queryKey)).toEqual([
    mapKeys.alerts("2026-10-04T21:00:00Z"), mapKeys.reports("2026-10-04T21:00:00Z", 21600, "all"),
  ]);
  await Promise.all(requests.map(({ queryFn }) => queryFn()));
  expect(call).toHaveBeenCalledWith("get_weather_alerts", { validTime: "2026-10-04T21:00:00Z" });
  expect(call).toHaveBeenCalledWith("get_storm_reports", { validTime: "2026-10-04T21:00:00Z", windowS: 21600, hazardFilter: "all" });
  expect(isLiveQuery(mapKeys.outlook("cat", "2026-10-04T21:00:00Z"))).toBe(false);
  const saved = currentPreferences();
  resetLayerSettings();
  applyPreferences(saved);
  expect(panelLayers(useMapStore.getState(), 0)).toMatchObject({ warnings: { visible: true }, watches: { visible: true }, "storm-reports": { visible: true } });
});

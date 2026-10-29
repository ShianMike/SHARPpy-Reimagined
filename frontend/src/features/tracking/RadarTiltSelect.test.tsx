// @vitest-environment jsdom
import fixture from "@contracts/public/map/radar-tilts.json";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseRadarTilts } from "../../lib/contracts/map";
import { useAppStore } from "../../stores/appStore";
import { useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { panelWeather, useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import { RadarControls } from "../../map/controls/OverlayControls";
import { usePanelRadar, useRadarSiteFrame } from "../../map/mapQueries";
import { LiveRadarControls } from "./LiveRadarControls";
import { RadarTiltSelect } from "./RadarTiltSelect";

const map = useMapStore.getState(), app = useAppStore.getState(), clock = useLiveTrackingStore.getState();
beforeEach(() => {
  useMapStore.setState(map, true); useAppStore.setState(app, true); useLiveTrackingStore.setState(clock, true);
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setRadarScope("site"); useMapStore.getState().setRadarSite("KTLX");
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function Picker() {
  const { radarSite, radarProduct, radarTilt } = useMapStore((state) => panelWeather(state));
  return <><RadarTiltSelect label="Site Tilt" site={radarSite} product={radarProduct ?? "reflectivity"} tilt={radarTilt} /><RadarTiltSelect label="Tilt" site={radarSite} product={radarProduct ?? "reflectivity"} tilt={radarTilt} /></>;
}

it("shares one check across both menus, omits unverified cuts and switches safely between sites and products", async () => {
  const transport = createTransport(), original = transport.call.bind(transport), calls: Record<string, unknown>[] = [];
  transport.call = (command, args) => {
    if (command !== "get_radar_tilts") return original(command, args);
    calls.push(args ?? {});
    const available = args?.site === "PGUA" ? ["a", "b"] : args?.product === "velocity" ? ["1", "3"] : ["0", "1"];
    return Promise.resolve({ ...fixture, siteId: args?.site, product: args?.product, tilts: fixture.tilts.map((cut) => ({ ...cut, status: available.includes(cut.tilt) ? "available" : "unavailable" })) });
  };
  const { view, queryClient } = renderWithBackend(<Picker />, transport);
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Tilt" }).textContent).toBe("0.5°"); });
  expect(calls).toHaveLength(1);
  fireEvent.click(screen.getByRole("combobox", { name: "Site Tilt" }));
  expect(screen.getAllByRole("option").map((item) => item.textContent)).toEqual(["0.5°", "1.3° / 1.5°"]);
  fireEvent.click(screen.getByRole("option", { name: "1.3° / 1.5°" }));
  act(() => { useMapStore.getState().setRadarProduct("velocity"); });
  await waitFor(() => { expect(calls).toHaveLength(2); });
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Tilt" }).textContent).toBe("1.3° / 1.5°"); });
  act(() => { useMapStore.getState().setPanelLayout(2); useMapStore.getState().setRadarSite("PGUA"); });
  await waitFor(() => { expect(panelWeather(useMapStore.getState()).radarTilt).toBe("a"); });
  expect(useMapStore.getState().livePanels[1]?.radarTilt).toBe("0");
  fireEvent.click(screen.getByRole("combobox", { name: "Tilt" }));
  expect(screen.getAllByRole("option").map((item) => item.textContent)).toEqual(["0.9°", "1.8°"]);
  view.unmount(); queryClient.clear();
});

it("distinguishes a failed check from no recent scans and addresses history explicitly", async () => {
  const transport = createTransport(), original = transport.call.bind(transport), calls: Record<string, unknown>[] = [];
  transport.call = (command, args) => {
    if (command !== "get_radar_tilts") return original(command, args);
    calls.push(args ?? {});
    return Promise.resolve({ ...fixture, siteId: args?.site, product: args?.product, tilts: fixture.tilts.map((cut) => ({ ...cut, status: args?.validTime === undefined ? "unknown" : "unavailable" })) });
  };
  const { view, queryClient } = renderWithBackend(<Picker />, transport);
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Tilt" }).textContent).toBe("Could not verify tilts"); });
  expect(screen.getByRole<HTMLButtonElement>("combobox", { name: "Site Tilt" }).disabled).toBe(true);
  act(() => { useLiveTrackingStore.setState({ selectedTime: Date.parse("2026-10-17T07:20:00Z") }); });
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Tilt" }).textContent).toBe("No recent scans"); });
  expect(calls.at(-1)?.validTime).toBe("2026-10-17T07:20:00Z");
  view.unmount(); queryClient.clear();
});

it("rejects duplicate, incomplete and invalid availability replies", () => {
  expect(parseRadarTilts(fixture).tilts).toHaveLength(7);
  for (const tilts of [fixture.tilts.slice(1), [...fixture.tilts.slice(1), fixture.tilts[1]], fixture.tilts.map((cut) => ({ ...cut, status: "maybe" }))]) expect(() => parseRadarTilts({ ...fixture, tilts })).toThrow();
});

it("shows lowest available for Australian sites without checking NOAA tilts", async () => {
  useMapStore.getState().setRadarSite("ABRD");
  const transport = createTransport(), call = vi.spyOn(transport, "call");
  const { view, queryClient } = renderWithBackend(<Picker />, transport);
  for (const name of ["Site Tilt", "Tilt"]) {
    const control = screen.getByRole<HTMLButtonElement>("combobox", { name });
    expect(control.textContent).toBe("Lowest available");
    expect(control.disabled).toBe(true);
    fireEvent.click(control);
  }
  await act(async () => { await Promise.resolve(); });
  expect(call).not.toHaveBeenCalled();
  view.unmount(); queryClient.clear();
});

it("stops offering previously verified cuts if refreshing their check fails", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const transport = createTransport(), original = transport.call.bind(transport);
  let fail = false;
  transport.call = (command, args) => command === "get_radar_tilts" && fail ? Promise.reject(new Error("offline")) : original(command, args);
  const { view, queryClient } = renderWithBackend(<Picker />, transport);
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Tilt" }).textContent).toBe("0.5°"); });
  fail = true;
  await act(async () => { await queryClient.invalidateQueries({ queryKey: ["radar-tilts"] }); });
  await waitFor(() => { expect(screen.getByRole("combobox", { name: "Site Tilt" }).textContent).toBe("Could not verify tilts"); });
  expect(screen.getByRole<HTMLButtonElement>("combobox", { name: "Site Tilt" }).disabled).toBe(true);
  view.unmount(); queryClient.clear();
});

it.each(["toolbar", "layer"])("filters %s products to Australian capabilities and disables elevation checks", async (controls) => {
  useAppStore.getState().setActiveWorkspace("live");
  useLiveTrackingStore.setState({ now: Date.parse("2026-10-17T08:00:00Z"), selectedTime: null });
  const state = useMapStore.getState();
  state.setPanelLayout(2);
  state.setRadarScope("site"); state.setRadarSite("KTLX"); state.setRadarProduct("velocity"); state.setRadarTilt("1");
  const otherPanel = useMapStore.getState().livePanels[0];
  state.setActivePanel(1); state.setRadarScope("site"); state.setRadarSite("KTLX"); state.setRadarProduct("velocity"); state.setRadarTilt("2");
  const transport = createTransport(), original = transport.call.bind(transport);
  transport.call = (command, args) => command === "get_radar_sites" ? Promise.resolve({ sites: [
    { id: "KTLX", latitude: 35.3333, longitude: -97.2783 },
    { id: "ABRD", name: "Broadmeadows", country: "AU", products: ["reflectivity"], latitude: -37.85, longitude: 144.75 },
  ] }) : command === "get_radar_site_frame" ? new Promise<unknown>(() => undefined) : original(command, args);
  const call = vi.spyOn(transport, "call");
  function Imagery() {
    const { site, product, tilt } = usePanelRadar();
    useRadarSiteFrame(product, site, true, "2026-10-17T08:00:00Z", tilt);
    return null;
  }
  const productLabel = controls === "toolbar" ? "Site Product · 2" : "Product";
  const { view, queryClient } = renderWithBackend(<>{controls === "toolbar" ? <LiveRadarControls /> : <RadarControls />}<Imagery /></>, transport);
  await waitFor(() => { expect(screen.getByRole("combobox", { name: productLabel }).textContent).toContain("Velocity"); });
  fireEvent.click(screen.getByRole("combobox", { name: "Site" }));
  const australian = await screen.findByRole("option", { name: "ABRD · Broadmeadows" });
  expect(australian.getAttribute("aria-description")).toContain("Australia");
  fireEvent.click(australian);
  expect(useMapStore.getState().focusRequest).toMatchObject({ stationId: "ABRD", position: { latitude: -37.85, longitude: 144.75 } });
  expect(screen.getByRole("combobox", { name: productLabel }).textContent).toContain("Base Reflectivity");
  fireEvent.click(screen.getByRole("combobox", { name: productLabel }));
  expect(screen.getAllByRole("option").map((option) => option.getAttribute("aria-label"))).toEqual(["Base Reflectivity"]);
  fireEvent.click(screen.getByRole("option", { name: "Base Reflectivity" }));
  const tilt = controls === "toolbar" ? screen.getByRole<HTMLButtonElement>("button", { name: "Site Tilt" })
    : screen.getByRole<HTMLButtonElement>("combobox", { name: "Tilt" });
  expect(tilt.textContent).toContain("Lowest available");
  expect(tilt.disabled).toBe(true);
  await waitFor(() => { expect(call).toHaveBeenCalledWith("get_radar_site_frame", { site: "ABRD", product: "reflectivity", validTime: "2026-10-17T08:00:00Z", tilt: "0" }); });
  const australianCalls = call.mock.calls.filter(([, args]) => args?.site === "ABRD");
  expect(australianCalls.length).toBeGreaterThan(0);
  expect(australianCalls.every(([command, args]) => command === "get_radar_site_frame" && args?.product === "reflectivity" && args.tilt === "0")).toBe(true);
  expect(useMapStore.getState().livePanels[0]).toBe(otherPanel);
  const focus = useMapStore.getState().focusRequest;
  fireEvent.click(screen.getByRole("combobox", { name: "Site" }));
  fireEvent.click(screen.getByRole("option", { name: controls === "toolbar" ? "Nearest site" : "Nearest to map centre" }));
  expect(useMapStore.getState().focusRequest).toBe(focus);
  if (controls === "toolbar") {
    fireEvent.click(screen.getByRole("combobox", { name: "Site" }));
    fireEvent.click(screen.getByRole("option", { name: "CONUS mosaic" }));
    expect(useMapStore.getState().focusRequest).toBe(focus);
  }
  view.unmount(); queryClient.clear();
});

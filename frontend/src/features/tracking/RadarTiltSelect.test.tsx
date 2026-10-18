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

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { createPortal } from "react-dom";
import { Popover } from "../../components/Popover";
import { SourceSelect } from "../../features/sounding/SourceFields";
import { afterEach, beforeEach, expect, it } from "vitest";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { LAYER_REGISTRY } from "../layers/descriptors";
import { ORDERABLE_LAYER_IDS } from "../layers/registry";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { renderWithBackend } from "../../test/backendHarness";
import { LayerPanel } from "./LayerPanel";
import { LayerRow } from "./LayerRow";
import { AvailabilityControl } from "./AvailabilityControl";
import { RadarControls, SatelliteControls } from "./OverlayControls";
import { ReportControls } from "./ReportControls";

const initial = useMapStore.getState();
beforeEach(() => { useMapStore.setState(initial, true); });
afterEach(cleanup);

it("keeps the Layers popup open when choosing or dismissing a provider menu", async () => {
  function Popup() {
    const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null), [target, setTarget] = useState<HTMLDivElement | null>(null);
    const [open, setOpen] = useState(false), [channel, setChannel] = useState("infrared");
    return <><button ref={setAnchor} onClick={() => { setOpen(true); }}>Layers</button><button>Outside</button>
      <Popover open={open} anchor={anchor} role="dialog" label="Layers" initialFocus="first" movable onClose={() => { setOpen(false); }}><div ref={setTarget} /></Popover>
      {target === null ? null : createPortal(<SourceSelect label="Channel" value={channel} options={[{ value: "infrared", label: "Infrared" }, { value: "visible", label: "Visible" }]} onChange={setChannel} />, target)}</>;
  }
  render(<Popup />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Layers" }));
  await user.click(screen.getByRole("combobox", { name: "Channel" }));
  await user.click(screen.getByRole("option", { name: "Visible" }));
  expect(screen.getByRole("dialog", { name: "Layers" })).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Channel" }).textContent).toBe("Visible");
  await user.click(screen.getByRole("combobox", { name: "Channel" }));
  await user.keyboard("{Escape}");
  expect(screen.getByRole("dialog", { name: "Layers" })).toBeTruthy();
  expect(screen.queryByRole("listbox")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Outside" }));
  await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Layers" })).toBeNull(); });
});

it("changes the shared projection from Map details and resets it with layers", async () => {
  renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport());
  await userEvent.click(screen.getByRole("button", { name: "Map details" }));
  expect(screen.queryByRole("button", { name: "Place names settings" })).toBeNull();
  const curved = screen.getByRole("button", { name: "Globe" });
  const flat = screen.getByRole("button", { name: "Flat map" });
  expect(flat.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(curved);
  expect(curved.getAttribute("aria-pressed")).toBe("true");
  expect(useMapStore.getState().projection).toBe("globe");
  await userEvent.click(screen.getByRole("button", { name: "Reset layers" }));
  expect(useMapStore.getState().projection).toBe("mercator");
});

it("shows the fixed live projection and keeps the Model Viewer projection choice", () => {
  useMapStore.setState({ projection: "globe", view: "live", layerPanelGroup: "map" });
  renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport());
  expect(screen.queryByRole("group", { name: "Map projection" })).toBeNull();
  expect(screen.getByText("Flat map")).toBeTruthy();
  act(() => { useMapStore.setState({ view: "map" }); });
  expect(screen.getByRole("button", { name: "Globe" }).getAttribute("aria-pressed")).toBe("true");
});

it("keeps opacity inside settings, with independent visibility and the active panel's values", async () => {
  useMapStore.setState({ view: "live" });
  useMapStore.getState().setPanelLayout(2);
  useMapStore.getState().setActivePanel(1);
  useMapStore.getState().setLayerVisible("radar", false);
  useMapStore.getState().setLayerVisible("satellite", true);
  const layer = LAYER_REGISTRY.find((item) => item.id === "radar");
  if (layer === undefined) throw new Error("Missing radar descriptor");
  function Row() {
    const state = useMapStore((store) => panelLayers(store, store.activePanel).radar);
    if (layer === undefined) throw new Error("Missing radar descriptor");
    return <LayerRow layer={layer} state={state} order={{ id: "radar", fromTop: 0, count: 2 }}>Provider status</LayerRow>;
  }
  render(<Row />);
  expect(screen.queryByRole("button", { name: "Radar mosaic settings" })).toBeNull();
  await userEvent.click(screen.getByRole("switch", { name: "Radar mosaic" }));
  const settings = screen.getByRole("button", { name: "Radar mosaic settings" });
  expect(settings.getAttribute("aria-expanded")).toBe("false");
  expect(panelLayers(useMapStore.getState(), 1).radar.visible).toBe(true);
  expect(panelLayers(useMapStore.getState(), 0).radar.visible).toBe(true);
  const details = document.getElementById(settings.getAttribute("aria-controls") ?? "");
  expect(details?.hidden).toBe(true);
  expect(screen.queryByRole("slider", { name: "Radar mosaic opacity" })).toBeNull();
  await userEvent.click(settings);
  expect(details?.hidden).toBe(false);
  fireEvent.change(screen.getByRole("slider", { name: "Radar mosaic opacity" }), { target: { value: "0.35" } });
  expect(panelLayers(useMapStore.getState(), 1).radar.opacity).toBe(0.35);
  expect(panelLayers(useMapStore.getState(), 0).radar.opacity).toBe(0.8);
  await userEvent.click(screen.getByRole("button", { name: "Move Radar mosaic down" }));
  expect(useMapStore.getState().layerOrder.indexOf("radar")).toBe(ORDERABLE_LAYER_IDS.indexOf("satellite"));
  await userEvent.click(settings);
  expect(details?.hidden).toBe(true);
  expect(screen.queryByRole("slider", { name: "Radar mosaic opacity" })).toBeNull();
  expect(panelLayers(useMapStore.getState(), 1).radar.visible).toBe(true);
});

it("leads with enabled layers and opens provider settings only when requested", async () => {
  useMapStore.setState({ view: "live" });
  useMapStore.getState().setLayerVisible("radar", false);
  useMapStore.getState().setLayerVisible("watches", true);
  useMapStore.getState().setLayerVisible("warnings", true);
  renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport("overlay-error"));
  const panel = screen.getByRole("region", { name: "Layers" });
  const switches = within(panel).getAllByRole("switch");
  expect(switches.slice(0, 2).map((toggle) => toggle.getAttribute("aria-label"))).toEqual(["NWS warnings", "NWS watches"]);
  expect(screen.queryByRole("button", { name: "Radar mosaic settings" })).toBeNull();
  await userEvent.click(within(panel).getByRole("switch", { name: "Radar mosaic" }));
  await waitFor(() => { expect(document.activeElement).toBe(within(panel).getByRole("switch", { name: "Radar mosaic" })); });
  expect(screen.getByRole("button", { name: "Radar mosaic settings" }).getAttribute("aria-expanded")).toBe("false");
  await userEvent.click(screen.getByRole("button", { name: "Radar mosaic settings" }));
  expect(screen.getByRole("combobox", { name: "Radar" })).toBeTruthy();
  await waitFor(() => { expect(screen.getByTestId("radar-error").textContent).toBeTruthy(); }, { timeout: 4000 });
  expect(within(panel).getAllByRole("switch").slice(0, 3).map((toggle) => toggle.getAttribute("aria-label"))).toEqual(["NWS warnings", "NWS watches", "Radar mosaic"]);
  await userEvent.click(within(panel).getByRole("switch", { name: "Radar mosaic" }));
  await waitFor(() => { expect(document.activeElement).toBe(within(panel).getByRole("switch", { name: "Radar mosaic" })); });
  expect(screen.queryByRole("button", { name: "Radar mosaic settings" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Map details" }));
  expect(within(panel).getByRole("switch", { name: "Station labels" })).toBeTruthy();
});

it("remembers its category after reopening and resets layers in every panel", async () => {
  const map = useMapStore.getState();
  map.setPanelLayout(4);
  map.setActivePanel(2);
  map.setLayerVisible("station-labels", true);
  map.setProduct("tmp-2m");
  map.setLayerPanelGroup("map");
  const { view } = renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport());
  expect(screen.getByRole("button", { name: "Map details" }).getAttribute("aria-pressed")).toBe("true");
  view.unmount();
  renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport());
  expect(screen.getByRole("button", { name: "Map details" }).getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(screen.getByRole("button", { name: "Reset layers" }));
  expect(screen.getByRole("status", { name: "Layer settings status" }).textContent).toBe("Layers reset · all panels");
  expect(screen.getByRole("button", { name: "Weather" }).getAttribute("aria-pressed")).toBe("true");
  expect(useMapStore.getState()).toMatchObject({ panelLayout: 4, activePanel: 2 });
  expect(useMapStore.getState().panels[1]?.productKey).toBe("tmp-2m");
  for (let panel = 0; panel < 4; panel++) expect(panelLayers(useMapStore.getState(), panel)["station-labels"].visible).toBe(false);
});

it("chooses provider settings through custom menus and preserves numeric and automatic values", async () => {
  renderWithBackend(<><RadarControls /><SatelliteControls /><ReportControls /><AvailabilityControl /></>, createTransport("overlay-error"));
  const radar = screen.getByRole("combobox", { name: "Radar" });
  await userEvent.click(radar);
  await userEvent.keyboard("{End}{Enter}");
  expect(useMapStore.getState().radarScope).toBe("site");
  expect(useMapStore.getState().radarProduct).toBeNull();
  expect(document.activeElement).toBe(radar);
  const site = await screen.findByRole("combobox", { name: "Site" });
  await userEvent.click(site);
  await userEvent.click(screen.getByRole("option", { name: "KTLX" }));
  expect(useMapStore.getState().radarSite).toBe("KTLX");
  await userEvent.click(site);
  await userEvent.keyboard("{Home}{Enter}");
  expect(useMapStore.getState().radarSite).toBeNull();
  const product = await screen.findByRole("combobox", { name: "Product" });
  await userEvent.click(product);
  const lastProduct = within(screen.getByRole("listbox", { name: "Product" })).getAllByRole("option").at(-1)?.getAttribute("value");
  await userEvent.keyboard("{End}{Enter}");
  expect(useMapStore.getState().radarProduct).toBe(lastProduct);
  await userEvent.click(radar);
  await userEvent.keyboard("{Home}{Enter}");
  expect(useMapStore.getState().radarProduct).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Site" })).toBeNull();

  const channel = screen.getByRole("combobox", { name: "Channel" });
  await userEvent.click(channel);
  await userEvent.keyboard("{End}{Enter}");
  expect(useMapStore.getState().satelliteChannel).toBe("visible");
  const window = await screen.findByRole("combobox", { name: "Event window" });
  await userEvent.click(window);
  await userEvent.keyboard("{Home}{Enter}");
  expect(useMapStore.getState().reportWindowS).toBe(7200);
  const hazard = screen.getByRole("combobox", { name: "Hazard" });
  await userEvent.click(hazard);
  await userEvent.click(screen.getByRole("option", { name: "Tornado" }));
  expect(useMapStore.getState().reportFilter).toBe("tornado");
  await userEvent.click(hazard);
  await userEvent.keyboard("{End}{Escape}");
  expect(useMapStore.getState().reportFilter).toBe("tornado");
  expect(document.activeElement).toBe(hazard);

  const availability = screen.getByRole("combobox", { name: "Availability" });
  await userEvent.click(availability);
  const release = within(screen.getByRole("listbox", { name: "Availability" })).getAllByRole("option")[1]?.getAttribute("value");
  await userEvent.keyboard("{ArrowDown}{Enter}");
  expect(useMapStore.getState().availabilityTime).toBe(release);
  await userEvent.click(availability);
  await userEvent.keyboard("{Home}{Enter}");
  expect(useMapStore.getState().availabilityTime).toBeNull();
});

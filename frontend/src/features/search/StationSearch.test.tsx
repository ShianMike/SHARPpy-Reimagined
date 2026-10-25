// @vitest-environment jsdom
import catalogFull from "@contracts/public/stations.catalog.full.json";
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { handleGlobalKey } from "../../app/shortcuts";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { parseStationCatalog } from "../../lib/contracts/stations";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import { displayName } from "../../stations/stationFormat";
import { searchStations } from "../../stations/stationSearch";
import { renderWithBackend } from "../../test/backendHarness";
import { StationSearch } from "./StationSearch";

const appInitial = useAppStore.getState();
const mapInitial = useMapStore.getState();
const golden = parseStationCatalog(catalogFull).stations;
const scrollIntoView = vi.fn();

beforeEach(() => {
  scrollIntoView.mockClear();
  Element.prototype.scrollIntoView = scrollIntoView;
  useAppStore.setState(appInitial, true);
  useMapStore.setState(mapInitial, true);
  document.addEventListener("keydown", handleGlobalKey);
});

afterEach(() => {
  document.removeEventListener("keydown", handleGlobalKey);
  cleanup();
});

async function setup(scenario: string | null = null) {
  const user = userEvent.setup();
  renderWithBackend(<StationSearch />, createTransport(scenario));
  expect(screen.queryByRole("combobox")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Search stations" }));
  const input = screen.getByRole("combobox", { name: "Search stations" });
  if (scenario === null) {
    await waitFor(() => {
      expect((input as HTMLInputElement).disabled).toBe(false);
    });
  }
  return { user, input: input as HTMLInputElement };
}

const options = (): HTMLElement[] => screen.queryAllByRole("option");

describe("StationSearch", () => {
  it("shows stations on open and filters them with the combobox", async () => {
    const { user, input } = await setup();
    const trigger = screen.getByRole("button", { name: "Search stations" });
    expect(trigger.getAttribute("title")).toBeNull();
    expect(trigger.querySelector('[data-side="bottom-end"]')?.textContent).toBe("Search stations");
    expect(input.getAttribute("aria-autocomplete")).toBe("list");
    expect(input.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("dialog", { name: "Station search" }).getAttribute("data-movable")).toBe("true");
    expect(options()).toHaveLength(8);
    expect(document.activeElement).toBe(input);
    expect(input.maxLength).toBe(64);
    await user.type(input, "norman");
    const listbox = screen.getByRole("listbox", { name: "Station results" });
    expect(input.getAttribute("aria-expanded")).toBe("true");
    expect(input.getAttribute("aria-controls")).toBe(listbox.id);
    expect(input.getAttribute("aria-activedescendant")).toBeNull();
    const expected = searchStations(golden, "norman").map((station) => station.id);
    expect(expected.length).toBeGreaterThan(0);
    expect(options().map((option) => option.getAttribute("aria-label")?.split(" ")[0])).toEqual(expected);
  });

  it("caps the list at 8 results, in parity with search_stations()", async () => {
    const { user, input } = await setup();
    await user.type(input, "a");
    expect(options()).toHaveLength(8);
    expect(options().map((option) => option.getAttribute("aria-label"))).toEqual(
      searchStations(golden, "a").map((station) => `${station.id} ${displayName(station.name)}`),
    );
  });

  it("finds radar IDs without case sensitivity and opens the active live panel at that site", async () => {
    useAppStore.getState().setActiveWorkspace("live");
    useAppStore.getState().selectPoint({ latitude: 36, longitude: -98 });
    useMapStore.getState().setPanelLayout(2); useMapStore.getState().setActivePanel(1);
    const layers = useMapStore.getState().layers;
    const { user } = await setup();
    await user.click(screen.getByRole("button", { name: "Radar sites" }));
    const input = screen.getByRole<HTMLInputElement>("combobox", { name: "Search radar sites" });
    await waitFor(() => { expect(input.disabled).toBe(false); });
    expect(document.activeElement).toBe(input);
    await user.type(input, "ktlx");
    expect(options()).toHaveLength(1);
    expect(options()[0]?.textContent).toContain("KTLX");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(useAppStore.getState()).toMatchObject({ activeWorkspace: "live", selectedPoint: { latitude: 36, longitude: -98 } });
    const state = useMapStore.getState();
    expect(state.livePanels[1]).toMatchObject({ radarScope: "site", radarSite: "KTLX", layers: { radar: { visible: true } } });
    expect(state.livePanels[0]?.radarSite).toBeNull();
    expect(state.layers).toBe(layers);
    expect(state.focusRequest).toMatchObject({ stationId: "KTLX", position: { latitude: 35.3333, longitude: -97.2783 } });
    expect(screen.queryByRole("dialog", { name: "Station search" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Search stations" }));
  });

  it("keeps single-site product choices and clears queries between sections", async () => {
    useAppStore.getState().setActiveWorkspace("live");
    useMapStore.getState().setRadarScope("site"); useMapStore.getState().setRadarProduct("velocity");
    const { user, input } = await setup();
    await user.type(input, "norman");
    await user.click(screen.getByRole("button", { name: "Radar sites" }));
    const radarInput = screen.getByRole<HTMLInputElement>("combobox", { name: "Search radar sites" });
    await waitFor(() => { expect(radarInput.disabled).toBe(false); });
    expect(radarInput.value).toBe("");
    await user.type(radarInput, "zzqx");
    expect(screen.getByRole("listbox").textContent).toBe('No radar sites match "zzqx"');
    await user.click(screen.getByRole("button", { name: "Clear search" }));
    expect(document.activeElement).toBe(radarInput);
    await user.type(radarInput, "KTLX");
    await user.click(screen.getByRole("option", { name: "KTLX WSR-88D radar" }));
    expect(useMapStore.getState().livePanels[0]?.radarProduct).toBe("velocity");
    expect(useAppStore.getState().selectedStationId).toBeNull();
  });

  it("disables radar-site search in Model Viewer and clears it when leaving Live Tracking", async () => {
    useAppStore.getState().setActiveWorkspace("map");
    const { user, input } = await setup();
    const radarSection = screen.getByRole("button", { name: "Radar sites" });
    expect(radarSection.hasAttribute("disabled")).toBe(true);
    await user.click(radarSection);
    expect(screen.queryByRole("combobox", { name: "Search radar sites" })).toBeNull();
    expect(input.disabled).toBe(false);
    act(() => { useAppStore.getState().setActiveWorkspace("live"); });
    expect(radarSection.hasAttribute("disabled")).toBe(false);
    await user.click(radarSection);
    const radarInput = screen.getByRole<HTMLInputElement>("combobox", { name: "Search radar sites" });
    await waitFor(() => { expect(radarInput.disabled).toBe(false); });
    await user.type(radarInput, "KTLX");
    act(() => { useAppStore.getState().setActiveWorkspace("map"); });
    expect(screen.getByRole<HTMLInputElement>("combobox", { name: "Search stations" }).value).toBe("");
    expect(radarSection.hasAttribute("disabled")).toBe(true);
    expect(useAppStore.getState().selectedStationId).toBeNull();
  });

  it("moves the active option with the arrow keys", async () => {
    const { user, input } = await setup();
    await user.type(input, "norman");
    await user.keyboard("{ArrowDown}");
    expect(scrollIntoView).toHaveBeenLastCalledWith({ block: "nearest" });
    const [first, second] = options();
    expect(input.getAttribute("aria-activedescendant")).toBe(first?.id);
    expect(first?.getAttribute("aria-selected")).toBe("true");
    await user.keyboard("{ArrowDown}{ArrowUp}");
    expect(input.getAttribute("aria-activedescendant")).toBe(first?.id);
    expect(second?.getAttribute("aria-selected")).toBe("false");
    await user.keyboard("{ArrowUp}");
    expect(input.getAttribute("aria-activedescendant")).toBe(first?.id);
  });

  it("Enter chooses the active option: selection and a focus request", async () => {
    const { user, input } = await setup();
    await user.type(input, "norman");
    const expected = searchStations(golden, "norman")[1]?.id;
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(useAppStore.getState().selectedStationId).toBe(expected);
    expect(useMapStore.getState().focusRequest).toEqual({ stationId: expected, seq: 1 });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Station search" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Search stations" }));
  });

  it("Enter without an active option chooses the first; an exact id matches alone", async () => {
    const { user, input } = await setup();
    await user.type(input, "72357");
    expect(options()).toHaveLength(1);
    await user.keyboard("{Enter}");
    expect(useAppStore.getState().selectedStationId).toBe("72357");
  });

  it("Esc dismisses search and returns focus without clearing the selected station", async () => {
    useAppStore.getState().selectStation("72357");
    const { user, input } = await setup();
    await user.type(input, "norman");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Station search" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Search stations" }));
    expect(useAppStore.getState().selectedStationId).toBe("72357");
    await user.keyboard("{Escape}");
    expect(useAppStore.getState().selectedStationId).toBeNull();
  });

  it("Tab reaches the clear action, then exits the popup", async () => {
    const { user, input } = await setup();
    await user.type(input, "norman");
    expect(screen.getByRole("listbox")).toBeTruthy();
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Clear search" }));
    await user.tab();
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Station search" })).toBeNull();
  });

  it("says when nothing matches", async () => {
    const { user, input } = await setup();
    await user.type(input, "  zzqx ");
    expect(screen.getByRole("listbox").textContent).toBe('No stations match "zzqx"');
  });

  it("chooses a result on click", async () => {
    const { user, input } = await setup();
    await user.type(input, "72357");
    await user.click(screen.getByRole("option", { name: "72357 Norman/Max Westheimer A, Ok., Usa" }));
    expect(useAppStore.getState().selectedStationId).toBe("72357");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Search stations" }));
  });

  it("is disabled with Stations unavailable without a catalogue", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { input } = await setup("python-unavailable");
    await waitFor(() => {
      expect(input.placeholder).toBe("Stations unavailable");
    });
    expect(input.disabled).toBe(true);
  });

  it("Ctrl+K and / open the search and focus its input", async () => {
    const { user } = await setup();
    await user.keyboard("{Escape}");
    await user.keyboard("{Control>}k{/Control}");
    expect(document.activeElement).toBe(screen.getByRole("combobox", { name: "Search stations" }));
    await user.keyboard("{Escape}");
    await user.keyboard("/");
    const input = screen.getByRole<HTMLInputElement>("combobox", { name: "Search stations" });
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe("");
  });

  it("keeps results during its exit and immediately reopens a fresh search with Ctrl+K", async () => {
    const { user, input } = await setup();
    await user.type(input, "norman");
    const dialog = screen.getByRole("dialog", { name: "Station search" });
    let finish = (): void => undefined;
    const finished = new Promise<void>((resolve) => { finish = resolve; });
    Object.defineProperty(dialog, "getAnimations", { value: () => [{ finished }] });
    await user.click(document.body);
    expect(screen.queryByRole("dialog", { name: "Station search" })).toBeNull();
    expect(dialog.hasAttribute("inert")).toBe(true);
    expect(input.value).toBe("norman");
    expect(dialog.querySelector('[role="listbox"]')).not.toBeNull();
    await user.keyboard("{Control>}k{/Control}");
    expect(screen.getByRole<HTMLInputElement>("combobox", { name: "Search stations" }).value).toBe("");
    await act(async () => { finish(); await finished; });
    expect(screen.getByRole("dialog", { name: "Station search" })).toBe(dialog);
    expect(document.activeElement).toBe(screen.getByRole("combobox", { name: "Search stations" }));
  });
});

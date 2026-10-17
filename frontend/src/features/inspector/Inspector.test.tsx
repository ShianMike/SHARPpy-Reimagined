// @vitest-environment jsdom
import catalogFull from "@contracts/public/stations.catalog.full.json";
import catalogSeed from "@contracts/public/stations.catalog.seed-fallback.json";
import availability from "@contracts/public/map/availability.json";
import unavailable from "@contracts/public/errors/STATION_LIST_UNAVAILABLE.json";
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useMapStore } from "../../stores/mapStore";
import { useSoundingStore } from "../../stores/soundingStore";
import { renderWithBackend, scriptedTransport } from "../../test/backendHarness";
import { Inspector } from "./Inspector";
import { catalogueText } from "./StationDetails";
import { StationTooltip } from "../../map/hover/StationTooltip";
import { useHoverStore } from "../../map/hover/hoverStore";
import { mapKeys } from "../../lib/backend/queryOptions";

const appInitial = useAppStore.getState();
const mapInitial = useMapStore.getState();
const soundingInitial = useSoundingStore.getState();
const forecastInitial = useForecastStore.getState();

beforeEach(() => {
  useAppStore.setState(appInitial, true);
  useMapStore.setState(mapInitial, true);
  useSoundingStore.setState(soundingInitial, true);
  useForecastStore.setState(forecastInitial, true);
  useHoverStore.getState().clear();
});

afterEach(() => {
  cleanup();
});

const inspector = (): HTMLElement | null => screen.queryByRole("complementary", { name: "Inspector" });

async function showStation(id: string, scenario: string | null = null): Promise<HTMLElement> {
  useAppStore.getState().selectStation(id);
  renderWithBackend(<Inspector />, createTransport(scenario));
  return screen.findByRole("complementary", { name: "Inspector" });
}

describe("Inspector", () => {
  it("shows an unavailable point field honestly and opens a sounding at the selected forecast time", async () => {
    useMapStore.getState().setLayerVisible("model-field", true);
    useForecastStore.getState().setHour(6);
    useAppStore.getState().selectPoint({ latitude: 35.18, longitude: -97.44 });
    renderWithBackend(<Inspector />, createTransport("field-error"));
    await waitFor(() => { expect(screen.getByTestId("point-value-1").textContent).toBe("Unavailable"); });
    expect(screen.getByTestId("point-forecast").textContent).toContain("F006");
    await userEvent.click(screen.getByRole("button", { name: "Open HRRR sounding here" }));
    expect(useAppStore.getState().activeWorkspace).toBe("sounding");
    expect(useSoundingStore.getState().model).toMatchObject({
      modelKey: "hrrr", runTime: "2026-10-04T16:00:00Z", forecastHour: 6,
      atStation: false, latitude: "35.1800", longitude: "-97.4400",
    });
  });

  it("is hidden without a selection, and while the catalogue loads", () => {
    renderWithBackend(<Inspector />, createTransport("slow-catalog"));
    expect(inspector()).toBeNull();
    act(() => {
      useAppStore.getState().selectStation("72357");
    });
    expect(inspector()).toBeNull();
  });

  it("shows only real catalogue fields for the selected station", async () => {
    const aside = await showStation("72357");
    await screen.findByText("Available");
    const text = aside.textContent;
    expect(text).toContain("72357");
    expect(text).toContain("Norman/Max Westheimer A, Ok., Usa");
    expect(text).toContain("Latitude35.18° N");
    expect(text).toContain("Longitude97.44° W");
    expect(text).toContain("AvailabilityAvailable");
    expect(text).toContain("UWyo reports a sounding for this launch.");
    expect(text).toContain("SourceUWyo FM35");
    expect(text).toContain("CatalogueBundled UWyo list, 934 stations");
    const source = screen.getByText("Station & source").closest("details");
    expect(source?.open).toBe(false);
    await userEvent.click(screen.getByText("Station & source"));
    expect(source?.open).toBe(true);
    expect(screen.getByText("UWyo FM35")).toBeTruthy();
    const id = screen.getByText("72357", { selector: "p" });
    expect(id.className).toContain("tabular");
  });

  it("names an unnamed station Unnamed station", async () => {
    const aside = await showStation("03023");
    expect(aside.textContent).toContain("Unnamed station");
    expect(aside.textContent).toContain("UWyo BUFR");
  });

  it("includes newly reporting stations with the seed fallback", async () => {
    const aside = await showStation("72357", "seed-fallback");
    await screen.findByText("Available");
    expect(aside.textContent).toContain("OUN Norman, OK");
    expect(aside.textContent).toContain("Seed list, 11 stations");
    expect(catalogueText("bundled", 1)).toBe("Bundled UWyo list, 1 station");
  });

  it("checks automatically once for all views, then updates them when the observed launch changes", async () => {
    useSoundingStore.getState().setObserved({ date: "2026-10-16", hour: 12 });
    useAppStore.getState().selectStation("72357");
    useHoverStore.getState().setHovered({ stationId: "72357", x: 100, y: 100 });
    let finish: (value: unknown) => void = () => { throw new Error("Availability request not started"); };
    const pending = new Promise<unknown>((resolve) => { finish = resolve; });
    const fixture = createTransport();
    const call = vi.fn((command: Parameters<typeof fixture.call>[0], args?: Readonly<Record<string, unknown>>) => {
      if (command !== "get_station_availability") return fixture.call(command, args);
      if (args?.observationTime === "2026-10-16T12:00:00Z") return pending;
      return Promise.resolve({ ...availability, observationTime: args?.observationTime, listedIds: [] });
    });
    renderWithBackend(<><Inspector /><StationTooltip /></>, { ...fixture, call });
    await screen.findByRole("complementary", { name: "Inspector" });
    expect(screen.getByRole("status").textContent).toContain("Checking…");
    expect(screen.getByRole("tooltip").textContent).toContain("Checking availability…");
    expect(call.mock.calls.filter(([command]) => command === "get_station_availability")).toEqual([["get_station_availability", { observationTime: "2026-10-16T12:00:00Z" }]]);
    await act(async () => { finish({ ...availability, observationTime: "2026-10-16T12:00:00Z" }); await pending; });
    await screen.findByText("Available");
    expect(screen.getByRole("status").textContent).toContain("2026-10-16 12Z");
    expect(screen.getByRole("tooltip").textContent).toContain("Availability: Available · 10-16 12Z");
    act(() => { useSoundingStore.getState().setObserved({ date: "2026-10-15", hour: 0 }); });
    await screen.findByText("Not reported");
    expect(screen.getByRole("status").textContent).toContain("2026-10-15 00Z");
    expect(screen.getByRole("tooltip").textContent).toContain("Availability: Not reported · 10-15 00Z");
    expect(call.mock.calls.filter(([command]) => command === "get_station_availability")).toHaveLength(2);
  });

  it("offers retry after a failed check and keeps a previous result if refreshing fails", async () => {
    useSoundingStore.getState().setObserved({ date: "2026-10-16", hour: 12 });
    useAppStore.getState().selectStation("72357");
    const report = { ...availability, observationTime: "2026-10-16T12:00:00Z" };
    const transport = scriptedTransport({ get_station_catalog: [catalogFull], get_station_availability: [{ reject: { ...unavailable, retryable: false } }, report, { reject: { ...unavailable, retryable: false } }] });
    const { queryClient } = renderWithBackend(<Inspector />, transport);
    await screen.findByText("Check failed");
    expect(screen.queryByText("Unknown")).toBeNull();
    expect(transport.calls("get_station_availability")).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Retry station availability check" }));
    await screen.findByText("Available");
    await act(async () => { await queryClient.refetchQueries({ queryKey: mapKeys.availability(report.observationTime), exact: true }); });
    expect(screen.getByRole("status").textContent).toContain("Available");
    await waitFor(() => { expect(screen.getByRole("status").textContent).toContain("Showing the last check."); });
    expect(screen.getByRole("status").textContent).toContain("2026-10-16 12Z");
    expect(screen.getByRole("button", { name: "Retry station availability check" })).toBeTruthy();
  });

  it("opens the same launch that an explicit availability check describes", async () => {
    useMapStore.getState().setAvailabilityTime("2026-10-14T00:00:00Z");
    await showStation("72357");
    await screen.findByText("Available");
    expect(screen.getByRole("status").textContent).toContain("2026-10-14 00Z");
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    expect(useSoundingStore.getState().observed).toMatchObject({ stationId: "72357", date: "2026-10-14", hour: 0 });
  });

  it("Clear selection clears the selection and hides the inspector", async () => {
    await showStation("72357");
    await userEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(useAppStore.getState().selectedStationId).toBeNull();
    expect(inspector()).toBeNull();
  });

  it("Zoom to station issues a focus request", async () => {
    await showStation("72357");
    await userEvent.click(screen.getByRole("button", { name: "Zoom to station" }));
    expect(useMapStore.getState().focusRequest).toEqual({ stationId: "72357", seq: 1 });
    expect(useAppStore.getState().selectedStationId).toBe("72357");
  });

  it("Open sounding fills the Observed form with the station and shows the Sounding workspace", async () => {
    useSoundingStore.getState().setSource("file");
    await showStation("72357");
    await userEvent.click(screen.getByRole("button", { name: "Open sounding" }));
    expect(useAppStore.getState().activeWorkspace).toBe("sounding");
    expect(useSoundingStore.getState().source).toBe("observed");
    expect(useSoundingStore.getState().observed.stationId).toBe("72357");
  });

  it("says so when a refetch removed the selected station, and offers Clear selection", async () => {
    const transport = scriptedTransport({ get_station_catalog: [catalogFull, catalogSeed] });
    const { queryClient } = renderWithBackend(<Inspector />, transport);
    await waitFor(() => {
      expect(transport.calls("get_station_catalog")).toBe(1);
    });
    act(() => {
      useAppStore.getState().selectStation("73111");
    });
    await screen.findByRole("complementary", { name: "Inspector" });
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["stations", "catalog"] });
    });
    const aside = await screen.findByText("Station 73111 is not in the current catalogue");
    expect(aside).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: "Clear selection" });
    expect(buttons).toHaveLength(2);
    await userEvent.click(buttons[1] as HTMLElement);
    expect(useAppStore.getState().selectedStationId).toBeNull();
  });
});

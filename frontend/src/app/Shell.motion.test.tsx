// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../lib/backend/fixtureTransport";
import { useAppStore } from "../stores/appStore";
import { useForecastStore } from "../stores/forecastStore";
import { useGraphicsStore } from "../stores/graphicsStore";
import { useMapStore } from "../stores/mapStore";
import { useUiStore } from "../stores/uiStore";
import { renderWithBackend } from "../test/backendHarness";
import { Shell } from "./Shell";

const { mapMounts } = vi.hoisted(() => ({ mapMounts: vi.fn() }));
vi.mock("../map/WeatherMap", async () => {
  const { useEffect } = await import("react");
  return { default: function MapStandIn() {
    useEffect(() => { mapMounts(); }, []);
    return <div data-testid="map-canvas" />;
  } };
});

const app = useAppStore.getState(), forecast = useForecastStore.getState(), graphics = useGraphicsStore.getState();
const map = useMapStore.getState(), ui = useUiStore.getState();
beforeEach(() => {
  useAppStore.setState(app, true); useForecastStore.setState(forecast, true);
  useGraphicsStore.setState(graphics, true);
  useMapStore.setState(map, true); useUiStore.setState(ui, true);
  mapMounts.mockClear();
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function pendingExit(element: HTMLElement) {
  let finish = (): void => undefined;
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  Object.defineProperty(element, "getAnimations", { value: () => [{ finished }] });
  return finish;
}

it("keeps a drawn map mounted through recoverable errors and falls back for startup failure", async () => {
  useAppStore.getState().setActiveWorkspace("map");
  const { queryClient } = renderWithBackend(<Shell />, createTransport());
  expect(screen.getByRole("status", { name: "Loading map" }).textContent).toContain("Preparing your weather workspace");
  const canvas = await screen.findByTestId("map-canvas");
  expect(screen.getByRole("status", { name: "Loading map" })).toBeTruthy();
  act(() => { useGraphicsStore.getState().setRendererReady(); });
  expect(screen.queryByRole("status", { name: "Loading map" })).toBeNull();
  expect(screen.getByTestId("map-canvas")).toBe(canvas);
  expect(mapMounts).toHaveBeenCalledTimes(1);
  act(() => { useGraphicsStore.getState().setRendererFailed("overlay", "Temporary shader failure"); });
  expect(useGraphicsStore.getState().panelErrors[0]?.message).toBe("Temporary shader failure");
  expect(screen.getByTestId("map-canvas")).toBe(canvas);
  expect(screen.queryByTestId("reduced-rendering")).toBeNull();
  act(() => { useGraphicsStore.getState().setRendererReady(); });
  expect(useGraphicsStore.getState().panelErrors[0]).toBeUndefined();
  expect(screen.getByTestId("map-canvas")).toBe(canvas);
  expect(mapMounts).toHaveBeenCalledTimes(1);
  act(() => { useGraphicsStore.getState().beginRendererAttempt(); });
  expect(screen.getByRole("status", { name: "Loading map" })).toBeTruthy();
  act(() => { useGraphicsStore.getState().setRendererFailed("timeout", "Test startup timeout"); });
  expect(screen.queryByRole("status", { name: "Loading map" })).toBeNull();
  expect(screen.getByTestId("reduced-rendering")).toBeTruthy();
  queryClient.clear();
});

it("hides Point controls only in Live Tracking and returns focus to Layers after closing station details", async () => {
  useAppStore.getState().setActiveWorkspace("map");
  const { queryClient } = renderWithBackend(<Shell />, createTransport());
  await screen.findByTestId("map-canvas");
  expect(screen.getByRole("button", { name: "Point details" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Live Tracking" }));
  expect(screen.queryByRole("button", { name: "Point details" })).toBeNull();
  const layers = screen.getByRole("button", { name: "Layers" });
  fireEvent.click(layers);
  expect(screen.getByRole("heading", { name: "Layers" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Show point details" })).toBeNull();
  act(() => { useAppStore.getState().selectStation("72357"); });
  expect(screen.getByRole("heading", { name: "Point" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close map details" }));
  expect(document.activeElement).toBe(layers);
  fireEvent.click(screen.getByRole("button", { name: "Model Viewer" }));
  expect(screen.getByRole("button", { name: "Point details" })).toBeTruthy();
  queryClient.clear();
});

it.each(["overview", "settings", "status", "sounding"] as const)("transitions from %s to Model Viewer without rebuilding the map", async (workspace) => {
  useAppStore.getState().setActiveWorkspace(workspace);
  const { queryClient } = renderWithBackend(<Shell />, createTransport());
  const canvas = await screen.findByTestId("map-canvas");
  const panel = screen.getByTestId("workspace-panel");
  const finish = pendingExit(panel);

  fireEvent.click(screen.getByRole("button", { name: "Model Viewer" }));
  expect(screen.getByTestId("map-canvas")).toBe(canvas);
  expect(canvas.parentElement?.hasAttribute("inert")).toBe(false);
  expect(screen.getByTestId("map-overlay").parentElement?.dataset.motion).toBe("fade");
  expect(panel.dataset.motion).toBe("panel");
  expect(panel.hidden).toBe(true);
  expect(panel.hasAttribute("inert")).toBe(true);
  expect(panel.getAttribute("aria-hidden")).toBe("true");
  expect(screen.getByTestId("workspace-panel")).toBe(panel);

  await act(async () => { finish(); await Promise.resolve(); });
  expect(screen.queryByTestId("workspace-panel")).toBeNull();
  expect(mapMounts).toHaveBeenCalledTimes(1);
  queryClient.clear();
});

it("ignores an old exit when another workspace opens before it finishes", async () => {
  useAppStore.getState().setActiveWorkspace("overview");
  const { queryClient } = renderWithBackend(<Shell />, createTransport());
  await screen.findByTestId("map-canvas");
  const finish = pendingExit(screen.getByTestId("workspace-panel"));
  fireEvent.click(screen.getByRole("button", { name: "Model Viewer" }));
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  await act(async () => { finish(); await Promise.resolve(); });
  expect(screen.getByTestId("workspace-panel").dataset.workspace).toBe("settings");
  expect(screen.getByTestId("workspace-panel").hasAttribute("inert")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Model Viewer" }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.queryByTestId("workspace-panel")).toBeNull();
  expect(mapMounts).toHaveBeenCalledTimes(1);
  queryClient.clear();
});

it("keeps the selected point and inspector state across model, parameter, layout and workspace changes", async () => {
  useAppStore.getState().setActiveWorkspace("map");
  const snapshot = { center: [-97.4567890123, 35.2345678901] as [number, number], zoom: 6.375 };
  useMapStore.getState().setCameraSnapshot(snapshot);
  const fixture = createTransport("field-error");
  let release!: () => void;
  const pendingCatalog = new Promise<void>((resolve) => { release = resolve; });
  const { view, queryClient } = renderWithBackend(<Shell />, { ...fixture, call: (command, args) =>
    command === "get_map_catalog" && args?.model === "rap" ? pendingCatalog.then(() => fixture.call(command, args)) : fixture.call(command, args),
  });
  try {
    await screen.findByTestId("map-canvas");
    act(() => { useAppStore.getState().selectPoint({ latitude: 35.2345678901, longitude: -97.4567890123 }); });
    const selected = useAppStore.getState().selectedPoint;
    const user = userEvent.setup();
    await user.click(await screen.findByText("Forecast & source"));
    expect(screen.getByText("Forecast & source").closest("details")?.open).toBe(true);
    act(() => { useForecastStore.getState().setModel("rap"); });
    expect(screen.queryByText("Forecast & source")).toBeNull();
    release();
    await waitFor(() => { expect(screen.getByText("Forecast & source").closest("details")?.open).toBe(true); });
    for (const layout of [2, 4, 1] as const) {
      act(() => { useMapStore.getState().setProduct("tmp-2m"); useMapStore.getState().setPanelLayout(layout); });
      expect(screen.getByText("Forecast & source").closest("details")?.open).toBe(true);
    }
    for (const workspace of ["overview", "settings", "status", "sounding", "live", "map"] as const) {
      act(() => { useAppStore.getState().setActiveWorkspace(workspace); });
      expect(useAppStore.getState().selectedPoint).toBe(selected);
      expect(useMapStore.getState().cameraSnapshot).toEqual(snapshot);
    }
    await waitFor(() => { expect(screen.getByText("Forecast & source").closest("details")?.open).toBe(true); });
    expect(screen.getByRole("button", { name: "Point details" }).getAttribute("aria-expanded")).toBe("true");
    await user.click(screen.getByRole("button", { name: "Close map details" }));
    act(() => {
      useForecastStore.getState().setModel("hrrr"); useMapStore.getState().setProduct("dpt-2m"); useMapStore.getState().setPanelLayout(2);
      useAppStore.getState().setActiveWorkspace("settings");
    });
    act(() => { useAppStore.getState().setActiveWorkspace("map"); });
    expect(screen.getByRole("button", { name: "Point details" }).getAttribute("aria-expanded")).toBe("false");
    await user.click(screen.getByRole("button", { name: "Layers" }));
    act(() => { useAppStore.getState().setActiveWorkspace("sounding"); });
    act(() => { useAppStore.getState().setActiveWorkspace("map"); });
    expect(screen.getByRole("button", { name: "Layers" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("heading", { name: "Layers" })).toBeTruthy();
    expect(useAppStore.getState().selectedPoint).toBe(selected);
    expect(screen.getByRole("dialog", { name: "Layers" }).getAttribute("data-movable")).toBe("true");
    await user.click(screen.getByRole("button", { name: "Close layers" }));
    await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Layers" })).toBeNull(); });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Layers" }));
    await user.click(screen.getByRole("button", { name: "Layers" }));
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Layers" }), { key: "Escape" });
    await waitFor(() => { expect(screen.queryByRole("dialog", { name: "Layers" })).toBeNull(); });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Layers" }));
    expect(useAppStore.getState().selectedPoint).toBe(selected);
  } finally { view.unmount(); queryClient.clear(); }
});

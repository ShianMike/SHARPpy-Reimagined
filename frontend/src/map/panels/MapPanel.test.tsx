// @vitest-environment jsdom
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { renderWithBackend } from "../../test/backendHarness";
import { MapPanel } from "./MapPanel";
import { useGraphicsStore } from "../../stores/graphicsStore";

vi.mock("../runtime/MapRuntime", () => ({ MapRuntime: class {
  map = { getCenter: () => ({ lat: 35.2345, lng: -97.4567 }) };
  constructor(container: HTMLElement) { const canvas = document.createElement("canvas"); canvas.tabIndex = 0; container.append(canvas); }
  dispose() {}
} }));
vi.mock("../panelScene", () => ({ usePanelScene: () => ({ blocked: "hidden", issues: [], retry: vi.fn() }) }));
vi.mock("../controls/ParameterScale", () => ({ ParameterScale: () => null }));
vi.mock("../hover/FieldTooltip", () => ({ FieldTooltip: () => null }));

afterEach(() => { cleanup(); useAppStore.setState(useAppStore.getInitialState(), true); useMapStore.setState(useMapStore.getInitialState(), true); });

it("shows a failed secondary panel and recovers without clearing products or the camera", () => {
  const initial = useGraphicsStore.getState();
  const map = useMapStore.getState();
  useAppStore.getState().setActiveWorkspace("map");
  renderWithBackend(<MapPanel panel={2} layout={1} />, createTransport());
  try {
    act(() => { useGraphicsStore.getState().setRendererFailed("overlay", "shader failed", 2); });
    expect(screen.getByRole("alert").textContent).toContain("Panel 3 could not be drawn");
    act(() => { useGraphicsStore.getState().setRendererReady(2); });
    expect(screen.queryByRole("alert")).toBeNull();
    act(() => { useGraphicsStore.getState().setRendererFailed("constructor", "context unavailable", 2); });
    fireEvent.click(screen.getByRole("button", { name: "Reload maps" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(useGraphicsStore.getState().mapAttempt).toBe(initial.mapAttempt + 1);
    expect(useMapStore.getState().panels).toBe(map.panels);
    expect(useMapStore.getState().cameraSnapshot).toBe(map.cameraSnapshot);
  } finally { useGraphicsStore.setState(initial, true); }
});

it("inspects the focused map centre on Enter while preserving Live Tracking's keyboard behavior", async () => {
  useAppStore.getState().setActiveWorkspace("map");
  renderWithBackend(<MapPanel panel={0} layout={1} />, createTransport());
  const map = screen.getByTestId("map");
  await waitFor(() => { expect(map.querySelector("canvas")).not.toBeNull(); });
  const canvas = map.querySelector("canvas");
  if (canvas === null) throw new Error("Map canvas missing");
  fireEvent.keyDown(canvas, { key: "ArrowRight" });
  expect(useAppStore.getState().selectedPoint).toBeNull();
  fireEvent.keyDown(canvas, { key: "Enter" });
  expect(useAppStore.getState().selectedPoint).toEqual({ latitude: 35.2345, longitude: -97.4567 });
  act(() => { useAppStore.getState().clearSelection(); useAppStore.getState().setActiveWorkspace("live"); });
  fireEvent.keyDown(canvas, { key: "Enter" });
  expect(useAppStore.getState().selectedPoint).toBeNull();
});

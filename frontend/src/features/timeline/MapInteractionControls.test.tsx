// @vitest-environment jsdom
import type { PickingInfo } from "@deck.gl/core";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { MapToolbar } from "../../map/controls/MapToolbar";
import { ForecastDrawingTools } from "../../map/forecastDrawing/ForecastDrawingTools";
import { useHoverStore } from "../../map/hover/hoverStore";
import { useAppStore } from "../../stores/appStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useMapStore } from "../../stores/mapStore";
import { hoverFromPick } from "../../map/runtime/stationScene";
import { renderWithBackend } from "../../test/backendHarness";
import { MapInteractionControls } from "./MapInteractionControls";

afterEach(() => {
  cleanup();
  useAppStore.setState(useAppStore.getInitialState(), true);
  useHoverStore.setState(useHoverStore.getInitialState(), true);
  useBoxSoundingStore.setState(useBoxSoundingStore.getInitialState(), true);
  useForecastDrawingStore.setState(useForecastDrawingStore.getInitialState(), true);
  useForecastStore.setState(useForecastStore.getInitialState(), true);
  useMapStore.setState(useMapStore.getInitialState(), true);
});

it("shares the mode, clears every hover readout and resumes inspection on the next pointer move", async () => {
  render(<><MapInteractionControls /><MapInteractionControls /></>);
  const groups = screen.getAllByRole("group", { name: "Map interaction" });
  const first = groups[0];
  if (first === undefined) throw new Error("Map interaction control missing");
  const pick = { x: 20, y: 30, coordinate: [-97, 35] } as PickingInfo;
  useHoverStore.setState({
    hovered: { stationId: "KOUN", x: 20, y: 30 },
    outlook: { label: "Warning", description: "Warning", title: "NWS", subtitle: "", x: 20, y: 30 },
    report: { label: "Wind", description: "Wind", caption: "", x: 20, y: 30 },
    pointer: { panel: 0, longitude: -97, latitude: 35, x: 20, y: 30 },
  });
  await userEvent.click(within(first).getByRole("button", { name: "Navigate" }));
  for (const group of groups) {
    expect(within(group).getByRole("button", { name: "Navigate" }).getAttribute("aria-pressed")).toBe("true");
    expect(group.querySelector("input")).toBeNull();
    expect(group.querySelectorAll('svg[aria-hidden="true"]')).toHaveLength(3);
  }
  hoverFromPick(pick, 0, { left: 0, top: 0 });
  expect(useHoverStore.getState()).toMatchObject({ inspecting: false, hovered: null, outlook: null, report: null, pointer: null });
  await userEvent.click(within(first).getByRole("button", { name: "Inspect" }));
  hoverFromPick(pick, 2, { left: 0, top: 0 });
  expect(useHoverStore.getState().pointer).toMatchObject({ panel: 2, longitude: -97, latitude: 35 });
  expect(groups.every((group) => within(group).getByRole("button", { name: "Inspect" }).getAttribute("aria-pressed") === "true")).toBe(true);
});

it("switches from soundings and playback into Draw Forecast and returns to Inspect", async () => {
  useAppStore.getState().setActiveWorkspace("map");
  useBoxSoundingStore.getState().setMode("box");
  useForecastStore.getState().setPlaying(true);
  const noop = () => undefined;
  const { view, queryClient } = renderWithBackend(<>
    <MapInteractionControls />
    <MapToolbar feedbackTarget={null} onZoomIn={noop} onZoomOut={noop} onResetView={noop} onLayoutChange={noop} />
  </>, createTransport());
  try {
    const draw = screen.getByRole("button", { name: "Draw Forecast" });
    await userEvent.click(draw);
    expect(draw.getAttribute("aria-pressed")).toBe("true");
    expect(useBoxSoundingStore.getState().mode).toBe("point");
    expect(useForecastStore.getState().playing).toBe(false);
    expect(useHoverStore.getState().inspecting).toBe(false);
    expect(screen.getByRole("group", { name: "Forecast drawing" }).dataset.motion).toBe("enter");
    expect(screen.queryByRole("group", { name: "Sounding selection" })).toBeNull();
    const inspect = screen.getByRole("button", { name: "Inspect" });
    await userEvent.click(inspect);
    expect(draw.getAttribute("aria-pressed")).toBe("false");
    expect(useHoverStore.getState().inspecting).toBe(true);
    expect(screen.getByRole("group", { name: "Sounding selection" }).dataset.motion).toBe("enter");
    expect(screen.queryByRole("group", { name: "Forecast drawing" })).toBeNull();
    expect(document.activeElement).toBe(inspect);
  } finally { view.unmount(); queryClient.clear(); }
});

it("keeps the Areas disclosure and drawing actions usable during their transitions", async () => {
  useForecastDrawingStore.setState((state) => ({
    enabled: true,
    polygons: { ...state.polygons, cat: [{ id: "slight", level: 2, hatch: false, points: [[-101, 38], [-99, 35], [-96, 38]] }] },
  }));
  const feedbackTarget = document.body.appendChild(document.createElement("div"));
  const { view, queryClient } = renderWithBackend(<ForecastDrawingTools feedbackTarget={feedbackTarget} />, createTransport());
  const user = userEvent.setup();
  try {
    const risk = screen.getByRole("button", { name: "Forecast risk" });
    await user.click(risk);
    const picker = within(screen.getByRole("dialog", { name: "Draw forecast" }));
    const areas = picker.getByRole("button", { name: "Areas", expanded: false });
    const manager = document.getElementById(areas.getAttribute("aria-controls") ?? "");
    expect(manager?.dataset.motion).toBe("disclosure");
    expect(manager?.hidden).toBe(true);
    await user.click(areas);
    const polygon = picker.getByRole("button", { name: "Area 1 · Slight" });
    await user.click(polygon);
    expect(polygon.getAttribute("aria-pressed")).toBe("true");
    await user.click(areas);
    expect(manager?.hidden).toBe(true);
    expect(manager?.hasAttribute("inert")).toBe(true);
    expect(picker.queryByRole("button", { name: "Area 1 · Slight" })).toBeNull();
    await user.click(areas);
    expect(manager?.hasAttribute("inert")).toBe(false);
    expect(picker.getByRole("button", { name: "Area 1 · Slight" })).toBe(polygon);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Draw forecast" })).toBeNull();
    expect(document.activeElement).toBe(risk);

    const draw = screen.getByRole("button", { name: "Draw area" });
    await user.click(draw);
    expect(screen.getByRole("button", { name: "Finish area" })).toBe(draw);
    const feedback = screen.getByRole("region", { name: "Draw Slight" });
    expect(feedback.dataset.motion).toBe("fade");
    const drawing = useForecastDrawingStore.getState();
    act(() => { drawing.addVertex([-98, 38]); });
    await user.click(within(feedback).getByRole("button", { name: "Undo" }));
    expect(useForecastDrawingStore.getState().points).toHaveLength(0);
    await user.click(within(feedback).getByRole("button", { name: "Redo" }));
    expect(useForecastDrawingStore.getState().points).toEqual([[-98, 38]]);
    await user.click(within(feedback).getByRole("button", { name: "Cancel drawing" }));
    expect(screen.getByRole("button", { name: "Draw area" })).toBe(draw);
    expect(document.activeElement).toBe(risk);
  } finally { view.unmount(); queryClient.clear(); feedbackTarget.remove(); }
});

it("leaves forecast drawing controls out of the live timeline", () => {
  useMapStore.setState({ view: "live" });
  render(<MapInteractionControls />);
  expect(screen.queryByRole("button", { name: "Draw Forecast" })).toBeNull();
});

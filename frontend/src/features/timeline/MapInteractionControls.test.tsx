// @vitest-environment jsdom
import type { PickingInfo } from "@deck.gl/core";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { useHoverStore } from "../../map/hover/hoverStore";
import { hoverFromPick } from "../../map/runtime/stationScene";
import { MapInteractionControls } from "./MapInteractionControls";

afterEach(() => { cleanup(); useHoverStore.setState(useHoverStore.getInitialState(), true); });

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
    expect(group.querySelectorAll('svg[aria-hidden="true"]')).toHaveLength(2);
  }
  hoverFromPick(pick, 0, { left: 0, top: 0 });
  expect(useHoverStore.getState()).toMatchObject({ inspecting: false, hovered: null, outlook: null, report: null, pointer: null });
  await userEvent.click(within(first).getByRole("button", { name: "Inspect" }));
  hoverFromPick(pick, 2, { left: 0, top: 0 });
  expect(useHoverStore.getState().pointer).toMatchObject({ panel: 2, longitude: -97, latitude: 35 });
  expect(groups.every((group) => within(group).getByRole("button", { name: "Inspect" }).getAttribute("aria-pressed") === "true")).toBe(true);
});

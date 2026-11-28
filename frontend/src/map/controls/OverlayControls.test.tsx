// @vitest-environment jsdom
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useMapStore } from "../../stores/mapStore";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { renderWithBackend } from "../../test/backendHarness";
import { AvailabilityControl } from "./AvailabilityControl";
import { RadarControls, SatelliteControls } from "./OverlayControls";
import { ReportControls } from "./ReportControls";

const initial = useMapStore.getState();
beforeEach(() => { useMapStore.setState(initial, true); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
  await userEvent.keyboard("{ArrowDown}{Enter}");
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

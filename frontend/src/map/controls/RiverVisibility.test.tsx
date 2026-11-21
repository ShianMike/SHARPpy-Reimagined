// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { panelLayers, useMapStore } from "../../stores/mapStore";
import { applyPreferences, createPreferencesRepository, DEFAULT_PREFERENCES, PREFERENCES_KEY, resetLayerSettings, startPreferenceSync } from "../../stores/preferences";
import { renderWithBackend } from "../../test/backendHarness";
import { LayerPanel } from "./LayerPanel";

afterEach(() => {
  cleanup();
  useMapStore.setState(useMapStore.getInitialState(), true);
  localStorage.removeItem(PREFERENCES_KEY);
});

it("keeps Rivers scoped to the active panel when switching between Flat map and Globe", async () => {
  useMapStore.getState().setPanelLayout(2);
  const { view, queryClient } = renderWithBackend(<LayerPanel availabilities={new Set()} />, createTransport());
  const user = userEvent.setup();
  expect(screen.queryByRole("switch", { name: "Rivers" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Map details" }));
  await user.click(screen.getByRole("button", { name: "Panel 2" }));
  const rivers = screen.getByRole("switch", { name: "Rivers" });
  expect(rivers.getAttribute("aria-checked")).toBe("true");
  await user.click(rivers);
  expect(panelLayers(useMapStore.getState(), 1).rivers.visible).toBe(false);

  const globe = screen.getByRole("button", { name: "Globe" });
  await user.click(globe);
  expect(globe.getAttribute("aria-pressed")).toBe("true");
  expect(rivers.getAttribute("aria-checked")).toBe("false");
  await user.click(screen.getByRole("button", { name: "Panel 1" }));
  expect(rivers.getAttribute("aria-checked")).toBe("true");
  await user.click(screen.getByRole("button", { name: "Panel 2" }));
  expect(rivers.getAttribute("aria-checked")).toBe("false");
  await user.click(rivers);
  expect(panelLayers(useMapStore.getState(), 1).rivers.visible).toBe(true);
  await user.click(screen.getByRole("button", { name: "Flat map" }));
  expect(rivers.getAttribute("aria-checked")).toBe("true");
  view.unmount();
  queryClient.clear();
});

it("defaults missing Rivers preferences and persists and resets them per panel and workspace", () => {
  const repository = createPreferencesRepository(() => localStorage);
  repository.save({ ...DEFAULT_PREFERENCES, panelLayout: 2 });
  applyPreferences(repository.load());
  const riverVisibility = () => [0, 1].map((panel) => panelLayers(useMapStore.getState(), panel).rivers.visible);
  expect(riverVisibility()).toEqual([true, true]);

  const stop = startPreferenceSync(repository);
  useMapStore.getState().setActivePanel(1);
  useMapStore.getState().setLayerVisible("rivers", false);
  useMapStore.setState({ view: "live" });
  expect(riverVisibility()).toEqual([true, true]);
  useMapStore.getState().setActivePanel(0);
  useMapStore.getState().setLayerVisible("rivers", false);
  stop();

  useMapStore.setState(useMapStore.getInitialState(), true);
  applyPreferences(repository.load());
  expect(riverVisibility()).toEqual([true, false]);
  useMapStore.setState({ view: "live" });
  expect(riverVisibility()).toEqual([false, true]);
  resetLayerSettings();
  expect(riverVisibility()).toEqual([true, true]);
  useMapStore.setState({ view: "map" });
  expect(riverVisibility()).toEqual([true, false]);
  resetLayerSettings();
  expect(riverVisibility()).toEqual([true, true]);
});

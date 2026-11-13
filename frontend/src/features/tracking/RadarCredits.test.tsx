// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Shell } from "../../app/Shell";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { renderWithBackend } from "../../test/backendHarness";

vi.mock("../../map/WeatherMap", () => ({ default: () => <div data-testid="map-canvas" /> }));

const app = useAppStore.getState(), forecast = useForecastStore.getState(), graphics = useGraphicsStore.getState();
const map = useMapStore.getState(), ui = useUiStore.getState();
beforeEach(() => {
  useAppStore.setState(app, true);
  useForecastStore.setState(forecast, true);
  useGraphicsStore.setState(graphics, true);
  useMapStore.setState(map, true);
  useUiStore.setState(ui, true);
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("renders radar credits on the bottom left only in Live Tracking", () => {
  useAppStore.getState().setActiveWorkspace("live");
  const { view, queryClient } = renderWithBackend(<Shell />, createTransport());
  const credits = screen.getByRole("complementary", { name: "Radar credits" });
  const scope = within(credits);
  expect(scope.getByRole("link", { name: "NOAA / NWS" }).getAttribute("href")).toBe("https://www.weather.gov/");
  expect(scope.getByRole("link", { name: "Australia’s Bureau of Meteorology" }).getAttribute("href")).toBe("https://www.bom.gov.au/");
  expect(scope.getByRole("link", { name: "Deutscher Wetterdienst" }).getAttribute("href")).toBe("https://www.dwd.de/");
  expect(scope.getByRole("link", { name: "Météo-France" }).getAttribute("href")).toBe("https://www.meteofrance.fr/");
  expect(scope.getByRole("link", { name: "EUMETNET OPERA" })).toBeTruthy();
  expect(scope.getByRole("link", { name: "MRRL InterRAD" }).getAttribute("href")).toBe("https://mrrl.net/");
  expect(scope.getByRole("link", { name: "source credits" }).getAttribute("href")).toBe("https://feeds.mrrl.net/ATTRIBUTION.txt");
  expect(scope.getByRole("link", { name: "Licence Ouverte 2.0" })).toBeTruthy();
  const shellCss = readFileSync(resolve(process.cwd(), "src/app/Shell.module.css"), "utf8");
  expect(shellCss).toMatch(/\.radarCredits\s*\{[^}]*left:\s*max\(var\(--chrome-gap\),\s*env\(safe-area-inset-left\)\)[^}]*bottom:\s*max\(var\(--chrome-gap\),\s*env\(safe-area-inset-bottom\)\)/s);
  act(() => { useAppStore.getState().setActiveWorkspace("map"); });
  expect(screen.queryByRole("complementary", { name: "Radar credits" })).toBeNull();
  act(() => { useAppStore.getState().setActiveWorkspace("sounding"); });
  expect(screen.queryByRole("complementary", { name: "Radar credits" })).toBeNull();
  act(() => { useAppStore.getState().setActiveWorkspace("live"); });
  expect(screen.getByRole("complementary", { name: "Radar credits" })).toBeTruthy();
  view.unmount();
  queryClient.clear();
});

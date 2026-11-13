// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import radar from "@contracts/public/map/radar.shrf?url&inline";
import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Shell } from "../../app/Shell";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { encodeShrf, parseShrf } from "../../lib/contracts/frames";
import { useAppStore } from "../../stores/appStore";
import { useForecastStore } from "../../stores/forecastStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { renderWithBackend } from "../../test/backendHarness";
import { RadarScale } from "./RadarScale";

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

it("displays radar source attribution at the bottom left following the visible frame in Live Tracking only", async () => {
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite("DESS");
  const transport = createTransport(), original = transport.call.bind(transport);
  const decoded = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
  transport.call = (command, args) => {
    if (command === "get_radar_site_frame") {
      const siteId = typeof args === "object" && args !== null && "siteId" in args ? String(args.siteId) : "DESS";
      const attribution = siteId === "DESS"
        ? "Datenbasis: Deutscher Wetterdienst, Rasterdaten bildlich wiedergegeben"
        : siteId === "NONE"
          ? ""
          : "Radar data: NOAA / NCEP";
      return Promise.resolve(encodeShrf({
        ...decoded.header, kind: "radar-site", siteId, product: "reflectivity", elevationAngle: null,
        label: "Precipitation-scan Reflectivity", attribution,
      }, decoded.body));
    }
    if (command === "get_radar_frame") {
      return Promise.resolve(encodeShrf({
        ...decoded.header, kind: "radar", product: "reflectivity",
        label: "Base Reflectivity", attribution: "Mosaic: NOAA / MRMS",
      }, decoded.body));
    }
    return original(command, args);
  };
  const { view, queryClient } = renderWithBackend(<RadarScale panel={0} />, transport);
  expect((await screen.findByTestId("radar-attribution")).textContent).toContain("Deutscher Wetterdienst");

  act(() => { useMapStore.getState().setRadarScope("mosaic"); });
  expect((await screen.findByTestId("radar-attribution")).textContent).toBe("Mosaic: NOAA / MRMS");

  act(() => { useMapStore.getState().setLayerVisible("radar", false); });
  expect(screen.queryByTestId("radar-attribution")).toBeNull();
  act(() => { useMapStore.getState().setLayerVisible("radar", true); });
  expect((await screen.findByTestId("radar-attribution")).textContent).toBe("Mosaic: NOAA / MRMS");

  act(() => { useAppStore.getState().setActiveWorkspace("map"); });
  expect(screen.queryByTestId("radar-attribution")).toBeNull();
  act(() => { useAppStore.getState().setActiveWorkspace("sounding"); });
  expect(screen.queryByTestId("radar-attribution")).toBeNull();
  act(() => { useAppStore.getState().setActiveWorkspace("live"); });
  expect((await screen.findByTestId("radar-attribution")).textContent).toBe("Mosaic: NOAA / MRMS");

  act(() => { useMapStore.getState().setRadarScope("site"); useMapStore.getState().setRadarSite("NONE"); });
  await act(async () => { await Promise.resolve(); });
  expect(screen.queryByTestId("radar-attribution")).toBeNull();

  const radarCss = readFileSync(resolve(process.cwd(), "src/features/tracking/RadarScale.module.css"), "utf8");
  expect(radarCss).toMatch(/\.attribution\s*\{[^}]*left:\s*max\(var\(--chrome-gap\),\s*env\(safe-area-inset-left\)\)[^}]*bottom:\s*max\(var\(--chrome-gap\),\s*env\(safe-area-inset-bottom\)\)[^}]*text-align:\s*left/s);
  view.unmount();
  queryClient.clear();
});

it("omits the separate Radar credits disclosure from the shell", () => {
  useAppStore.getState().setActiveWorkspace("live");
  const { view, queryClient } = renderWithBackend(<Shell />, createTransport());
  expect(screen.queryByRole("complementary", { name: "Radar credits" })).toBeNull();
  expect(screen.queryByText("Radar credits")).toBeNull();
  view.unmount();
  queryClient.clear();
});

// @vitest-environment jsdom
import radar from "@contracts/public/map/radar.shrf?url&inline";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { createTransport } from "../../lib/backend/fixtureTransport";
import { encodeShrf, parseShrf } from "../../lib/contracts/frames";
import { useAppStore } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import { renderWithBackend } from "../../test/backendHarness";
import { RadarScale } from "./RadarScale";

afterEach(cleanup);

it("displays radar source attribution alongside the map", async () => {
  useAppStore.getState().setActiveWorkspace("live");
  useMapStore.getState().setRadarScope("site");
  useMapStore.getState().setRadarSite("DESS");
  const transport = createTransport(), original = transport.call.bind(transport);
  const decoded = parseShrf(Uint8Array.from(atob(radar.split(",")[1] ?? ""), (character) => character.charCodeAt(0)).buffer);
  transport.call = (command, args) => command === "get_radar_site_frame"
    ? Promise.resolve(encodeShrf({ ...decoded.header, kind: "radar-site", siteId: "DESS", product: "reflectivity", elevationAngle: null,
      label: "Precipitation-scan Reflectivity", attribution: "Datenbasis: Deutscher Wetterdienst, Rasterdaten bildlich wiedergegeben" }, decoded.body))
    : original(command, args);
  const { queryClient } = renderWithBackend(<RadarScale panel={0} />, transport);
  expect((await screen.findByTestId("radar-attribution")).textContent).toContain("Deutscher Wetterdienst");
  queryClient.clear();
});

// @vitest-environment jsdom
import type { Layer } from "@deck.gl/core";
import { IconLayer, SolidPolygonLayer, TextLayer } from "@deck.gl/layers";
import type { MapLibreOverlay } from "@deck.gl/maplibre";
import { afterEach, expect, it, vi } from "vitest";
import outlookFixture from "@contracts/public/map/outlook.cat.json";
import reportsFixture from "@contracts/public/map/reports.json";
import { parseSpcOutlook } from "../../lib/contracts/map";
import { parseStormReports } from "../../lib/contracts/reports";
import { useMapStore } from "../../stores/mapStore";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import { readCanvasTokens } from "../canvasTokens";
import { StationScene } from "./stationScene";

afterEach(() => { useMapStore.setState(useMapStore.getInitialState(), true); });

it.each(["mercator", "globe"] as const)("keeps annotations readable over terrain without flattening weather geometry in %s", (projection) => {
  useMapStore.setState({ projection });
  useMapStore.getState().setLayerVisible("spc-cat", true);
  useMapStore.getState().setLayerVisible("storm-reports", true);
  const submit = vi.fn<(props: { layers: Layer[] }) => void>();
  const read = cssTokenReader(tokensCss);
  const scene = new StationScene({ setProps: submit } as unknown as MapLibreOverlay,
    readCanvasTokens(read), read("--font-mono"), 0, vi.fn());
  const outlook = parseSpcOutlook(outlookFixture).outlook;
  if (outlook === null) throw new Error("Missing outlook fixture");
  scene.setData({ outlooks: { cat: outlook }, reports: parseStormReports(reportsFixture).reports });
  scene.render();
  const layers = submit.mock.calls[0]?.[0].layers ?? [];
  const annotations: Layer[] = layers.filter((layer) => layer instanceof TextLayer || layer instanceof IconLayer);
  expect(annotations.map((layer) => layer.id)).toEqual(expect.arrayContaining(["spc-cat-labels", "storm-reports"]));
  for (const layer of annotations) {
    if (projection === "globe") {
      expect(layer.props.parameters).toMatchObject({ cullMode: "none", depthCompare: "always", depthWriteEnabled: false });
    } else expect(layer.props.parameters).not.toHaveProperty("depthCompare", "always");
  }
  const polygons: Layer[] = layers.filter((layer) => layer instanceof SolidPolygonLayer);
  expect(polygons.length).toBeGreaterThan(0);
  for (const layer of polygons) expect(layer.props.parameters).not.toHaveProperty("depthCompare", "always");
});

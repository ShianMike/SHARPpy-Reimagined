// @vitest-environment jsdom
import { IconLayer, TextLayer } from "@deck.gl/layers";
import type { PickingInfo } from "@deck.gl/core";
import fixture from "@contracts/public/map/reports.json";
import { afterEach, describe, expect, it } from "vitest";
import { parseStormReports } from "../../lib/contracts/reports";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import type { ReportPoint } from "../../types/map";
import { readCanvasTokens } from "../canvasTokens";
import { useHoverStore } from "../hover/hoverStore";
import { hoverFromPick } from "../runtime/stationScene";
import { LAYER_REGISTRY } from "./descriptors";
import { buildDeckLayers, defaultLayerStates, NO_SCENE_DATA, type LayerBuildContext } from "./registry";
import { buildReportLayers, REPORT_ICONS } from "./StormReportLayer";

const read = cssTokenReader(tokensCss);
const reports = parseStormReports(fixture).reports;
const visible = { visible: true, opacity: 1 };
const ctx: LayerBuildContext = {
  ...NO_SCENE_DATA, reports, stations: [], labels: null, selected: null,
  tokens: readCanvasTokens(read), labelFont: read("--font-mono"), placeLabels: [],
  span: 12, basemap: visible, weatherShown: true,
};
type Accessor<T> = (report: ReportPoint) => T;
afterEach(() => { useHoverStore.getState().clear(); });

describe("storm report readability", () => {
  it("draws distinct geometric symbols with a contrast edge, retaining provider colours and report order", () => {
    const layers = buildReportLayers(ctx, visible);
    const icons = layers.filter((layer): layer is IconLayer<ReportPoint> => layer instanceof IconLayer);
    expect(icons).toHaveLength(3);
    const svg = new DOMParser().parseFromString(decodeURIComponent(REPORT_ICONS.iconAtlas.split(",")[1] ?? ""), "image/svg+xml");
    expect(svg.querySelector("parsererror")).toBeNull();
    expect(new Set([...svg.querySelectorAll("path")].map((path) => path.getAttribute("d"))).size).toBe(4);
    const body = icons.find((layer) => layer.id === "storm-reports");
    if (body === undefined) throw new Error("Missing report picking layer");
    expect(body.props.pickable).toBe(true);
    const getIcon = body.props.getIcon as Accessor<string>;
    expect(reports.map(getIcon)).toEqual(["circle-stroke", "square-stroke", "diamond-stroke", "triangle-stroke"]);
    const wind = reports[0] as ReportPoint;
    expect(getIcon({ ...wind, symbol: "toString" })).toBe("circle-stroke");
    for (const icon of icons) {
      expect(icon.props.data).toBe(reports);
      expect(icon.props.sizeUnits).toBe("pixels");
      expect((icon.props.getSize as Accessor<number>)(wind)).toBeGreaterThanOrEqual(20);
      expect(icon.props.iconMapping).toBe(REPORT_ICONS.iconMapping);
    }
    expect((body.props.getColor as Accessor<readonly [number, number, number, number]>)(wind)).toEqual([0, 51, 127, 255]);
    expect((icons[2]?.props.getColor as Accessor<readonly [number, number, number, number]>)(wind)).toEqual([60, 124, 255, 255]);
    expect((icons[0]?.props.getColor as Accessor<readonly [number, number, number, number]>)(wind)).toEqual(ctx.tokens.colours.labelText);
    const dimmed = buildReportLayers(ctx, { visible: false, opacity: 0.4 });
    expect(dimmed.every((layer) => !layer.props.visible && layer.props.opacity === 0.4)).toBe(true);
    expect(dimmed[0]?.props.data).toBe(reports);
    expect((dimmed[0] as IconLayer).props.iconMapping).toBe(REPORT_ICONS.iconMapping);
    expect(buildReportLayers({ ...ctx, reports: null }, visible)).toEqual([]);
  });

  it("separates captions from symbols and reveals nearby captions as the map zooms in", () => {
    const wind = reports[0] as ReportPoint, tornado = reports[3] as ReportPoint;
    const data = [{ ...wind, longitude: 0 }, { ...wind, longitude: 3 }, { ...tornado, longitude: 1 }];
    const labelsAt = (zoom: number) => buildReportLayers({ ...ctx, reports: data, outlookLabelLayout: {
      width: 1000, height: 300, occupied: [], project: ([lon]) => ({ x: 100 + lon * zoom, y: 100 }),
    } }, visible).find((layer): layer is TextLayer<ReportPoint> => layer instanceof TextLayer);
    const labels = labelsAt(50);
    if (labels === undefined) throw new Error("Missing captions");
    expect(labels.props.data).toEqual([data[2]]);
    expect(Array.from(labelsAt(200)?.props.data as ReportPoint[])).toHaveLength(3);
    expect(labels.props.getColor).toEqual(ctx.tokens.colours.labelText);
    expect(labels.props.outlineColor).toEqual(ctx.tokens.colours.labelHalo);
    expect((labels.props.getPixelOffset as Accessor<readonly [number, number]>)(wind)[0]).toBeGreaterThan(20 / 2);
    expect(buildReportLayers({ ...ctx, span: 40 }, visible).at(-1)?.props.visible).toBe(false);
    expect(data.map((report) => report.longitude)).toEqual([0, 3, 1]);
  });

  it("keeps report symbols and captions above weather fills and native outlines", () => {
    const states = defaultLayerStates();
    states["storm-reports"].visible = true;
    states["spc-cat"].visible = true;
    const layers = buildDeckLayers(LAYER_REGISTRY, states, { ...ctx, weatherBeforeId: "waterway", outlooks: { cat: {
      title: "", subtitle: "", validFrom: null, validTo: null, issued: null, covers: true, sourceUrl: "", attribution: "", areas: [],
    } } }, ["storm-reports", "spc-cat"]);
    const fill = layers.findIndex((layer) => layer.id === "spc-cat-fill");
    expect(fill).toBeGreaterThanOrEqual(0);
    for (const layer of layers.filter((item) => item.id.startsWith("storm-reports"))) {
      expect(layers.indexOf(layer)).toBeGreaterThan(fill);
      expect(layer.props).not.toHaveProperty("beforeId");
    }
  });

  it.each(["storm-reports", "storm-reports-captions"])("opens and clears the full report when hovering %s", (id) => {
    const report = reports[0] as ReportPoint;
    const info = { layer: { id }, object: report, x: 100, y: 50 } as unknown as PickingInfo;
    hoverFromPick(info, 0, { left: 20, top: 10 });
    expect(useHoverStore.getState().report).toEqual({ label: report.label, description: report.description, caption: report.caption, x: 120, y: 60 });
    hoverFromPick({ ...info, object: null }, 0, { left: 20, top: 10 });
    expect(useHoverStore.getState().report).toBeNull();
  });
});

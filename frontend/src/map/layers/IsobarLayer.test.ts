// @vitest-environment jsdom
import { IconLayer, PathLayer, TextLayer } from "@deck.gl/layers";
import { describe, expect, it } from "vitest";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import type { ContourLines } from "../../types/map";
import { readCanvasTokens } from "../canvasTokens";
import { CONTOURS_LAYER_ID } from "./ContourLayer";
import { LAYER_REGISTRY } from "./descriptors";
import { buildIsobarLayers, ISOBARS_LAYER_ID } from "./IsobarLayer";
import type { WindBarb } from "./WindBarbLayer";
import { buildDeckLayers, defaultLayerStates, layerInView, NO_SCENE_DATA, type LayerBuildContext } from "./registry";

const read = cssTokenReader(tokensCss);
const pressure: ContourLines = {
  product: "mslp", runTime: "2026-11-06T00:00:00Z", forecastHour: 6,
  unit: "hPa", interval: 4, wind: null,
  segments: [[[-100, 35], [-99, 36]], [[-99, 36], [-98, 36.5]]],
};
const ctx: LayerBuildContext = {
  ...NO_SCENE_DATA, isobars: pressure, stations: [], labels: null, selected: null,
  tokens: readCanvasTokens(read), labelFont: read("--font-mono"), placeLabels: [], span: 360,
  basemap: { visible: true, opacity: 1 }, weatherShown: false,
};

describe("independent isobars", () => {
  it("starts off with its own Model Viewer visibility and opacity controls", () => {
    expect(defaultLayerStates().isobars).toEqual({ visible: false, opacity: 1 });
    expect(LAYER_REGISTRY.find(({ id }) => id === "isobars")).toMatchObject({
      label: "Isobars", userControllable: true, opacityControl: true,
    });
    expect(layerInView("isobars", "map")).toBe(true);
    expect(layerInView("isobars", "live")).toBe(false);
  });

  it("draws the actual pressure segments twice with contrasting pixel-width strokes", () => {
    const layers = buildIsobarLayers(ctx, { visible: true, opacity: 0.65 });
    expect(layers.map(({ id }) => id)).toEqual(["isobars-halo", ISOBARS_LAYER_ID]);
    for (const layer of layers) {
      expect(layer).toBeInstanceOf(PathLayer);
      if (!(layer instanceof PathLayer)) throw new Error("Expected pressure lines without inferred labels");
      expect(layer.props.data).toBe(pressure.segments);
      expect(layer.props).toMatchObject({ visible: true, opacity: 0.65, pickable: false, widthUnits: "pixels" });
      const getPath = layer.props.getPath;
      if (typeof getPath !== "function") throw new Error("Expected an isobar path accessor");
      for (const [index, segment] of pressure.segments.entries()) {
        expect(getPath(segment, { index, data: pressure.segments, target: [] })).toEqual(segment);
      }
    }
    expect(layers[0]?.props.getColor).toEqual(ctx.tokens.colours.labelHalo);
    expect(layers[1]?.props.getColor).toEqual(ctx.tokens.colours.labelText);
    expect(layers[0]?.props.getColor).not.toEqual(layers[1]?.props.getColor);
    const [halo, line] = layers;
    if (!(halo instanceof PathLayer) || !(line instanceof PathLayer)) throw new Error("Expected outlined pressure paths");
    expect(halo.props.getWidth).toBeGreaterThan(Number(line.props.getWidth));
  });

  it("prints only native pressure values at their supplied anchors, with outlined readable text", () => {
    const labels = [{ position: [-99, 36], value: 1004 }, { position: [-98, 36.5], value: 1012 }] as const;
    const layers = buildIsobarLayers({ ...ctx, isobars: { ...pressure, labels } }, { visible: true, opacity: 0.65 });
    const text = layers.find((layer): layer is TextLayer => layer instanceof TextLayer);
    expect(layers.map(({ id }) => id)).toEqual(["isobars-halo", ISOBARS_LAYER_ID, "isobars-labels"]);
    expect(text?.props.data).toBe(labels);
    expect(text?.props).toMatchObject({ visible: true, opacity: 0.65, pickable: false, sizeUnits: "pixels",
      getColor: ctx.tokens.colours.labelText, outlineColor: ctx.tokens.colours.labelHalo,
      outlineWidth: ctx.tokens.lengths.labelHaloWidth, fontFamily: ctx.labelFont });
    if (text === undefined || typeof text.props.getText !== "function" || typeof text.props.getPosition !== "function") throw new Error("Expected native pressure label accessors");
    for (const [index, label] of labels.entries()) {
      const info = { index, data: labels, target: [] };
      expect(text.props.getText(label, info)).toBe(String(label.value));
      expect(text.props.getPosition(label, info)).toEqual(label.position);
    }
    const legacy = buildIsobarLayers(ctx, { visible: true, opacity: 1 });
    const empty = buildIsobarLayers({ ...ctx, isobars: { ...pressure, labels: [] } }, { visible: true, opacity: 1 });
    expect([...legacy, ...empty].some((layer) => layer instanceof TextLayer)).toBe(false);
  });

  it("draws a native smooth path as a continuous stroke with its on-path label", () => {
    const paths = [[[-100, 35], [-99.75, 35.5], [-99.25, 35.75], [-99, 36]]] as const;
    const labels = [{ position: [-99.5, 35.625], value: 1012 }] as const;
    const layers = buildIsobarLayers({ ...ctx, isobars: { ...pressure, segments: [], paths, labels } }, { visible: true, opacity: 1 });
    const line = layers.find((layer): layer is PathLayer => layer instanceof PathLayer && layer.id === ISOBARS_LAYER_ID);
    expect(line?.props.data).toBe(paths);
    if (typeof line?.props.getPath !== "function") throw new Error("Expected a connected path accessor");
    expect(line.props.getPath(paths[0], { index: 0, data: paths, target: [] })).toEqual(paths[0]);
    expect(layers.at(-1)?.props.data).toBe(labels);
  });

  it("declutters native anchors in screen space and avoids occupied readings and clipped edges", () => {
    const labels = [
      { position: [-100, 35], value: 1000 }, { position: [-99.9, 35.1], value: 1004 },
      { position: [-96, 38], value: 1008 }, { position: [-90, 40], value: 1012 },
      { position: [-104, 35], value: 1016 }, { position: [-80, 40], value: 1020 },
    ] as const;
    const context = { ...ctx, isobars: { ...pressure, labels }, outlookLabelLayout: {
      width: 360, height: 240, occupied: [{ x: 160, y: 140, width: 60, height: 30 }],
      project: ([lon, lat]: readonly [number, number]) => ({ x: (lon + 100) * 20 + 80, y: (lat - 35) * 20 + 80 }),
    } };
    const states = defaultLayerStates(); states.isobars.visible = true;
    const layers = buildDeckLayers(LAYER_REGISTRY, states, context);
    const text = layers.find((layer): layer is TextLayer => layer instanceof TextLayer && layer.id === "isobars-labels");
    expect(text?.props.data).toEqual([labels[0], labels[3]]);
    expect(layers.at(-1)?.id).toBe("isobars-labels");
    expect(context.outlookLabelLayout.occupied).toHaveLength(1);
    states.isobars = { visible: false, opacity: 0.4 };
    const hidden = buildDeckLayers(LAYER_REGISTRY, states, context).find(({ id }) => id === "isobars-labels");
    expect(hidden?.props).toMatchObject({ visible: false, opacity: 0.4 });
  });

  it("keeps geometry and accessors on visibility and opacity changes, refreshing with pressure data", () => {
    const initial = buildIsobarLayers(ctx, { visible: true, opacity: 1 });
    const hidden = buildIsobarLayers(ctx, { visible: false, opacity: 0.35 });
    for (const [index, layer] of hidden.entries()) {
      const original = initial[index];
      if (!(layer instanceof PathLayer) || !(original instanceof PathLayer)) throw new Error("Expected pressure paths");
      expect(layer.props).toMatchObject({ visible: false, opacity: 0.35 });
      expect(layer.props.data).toBe(original.props.data);
      expect(layer.props.getPath).toBe(original.props.getPath);
    }
    const updated = { ...pressure, forecastHour: 9, segments: pressure.segments.slice(1) };
    const refreshed = buildIsobarLayers({ ...ctx, isobars: updated }, { visible: true, opacity: 1 });
    expect(refreshed[0]?.props.data).toBe(updated.segments);
    expect(refreshed[0]?.props.data).not.toBe(initial[0]?.props.data);
    expect(buildIsobarLayers({ ...ctx, isobars: null }, { visible: true, opacity: 1 })).toEqual([]);
    expect(buildIsobarLayers({ ...ctx, isobars: { ...pressure, segments: [] } }, { visible: true, opacity: 1 })).toEqual([]);
  });

  it("keeps product contours and wind separate when toggling, dimming or reordering isobars", () => {
    const productContours: ContourLines = { ...pressure, product: "hgt-wind-500", unit: "dam", interval: 6,
      segments: [[[-90, 30], [-89, 31]]] };
    const context: LayerBuildContext = { ...ctx, contours: productContours,
      contourSpec: { interval: 6, label: "500 hPa height (dam)", colour: "#ff0000", width: 1 },
      windBarbs: [{ position: [-95, 35], direction: 270, speed: 65 }], weatherBeforeId: "waterway" };
    const states = defaultLayerStates();
    states["model-field"] = { visible: true, opacity: 0.8 };
    states.isobars = { visible: false, opacity: 0.25 };
    const layers = buildDeckLayers(LAYER_REGISTRY, states, context);
    expect(layers.find(({ id }) => id === ISOBARS_LAYER_ID)?.props).toMatchObject({ visible: false, opacity: 0.25, beforeId: "waterway" });
    expect(layers.find(({ id }) => id === CONTOURS_LAYER_ID)?.props).toMatchObject({ visible: true, opacity: 0.8 });
    const wind = layers.filter((layer): layer is IconLayer<WindBarb> => layer instanceof IconLayer);
    expect(wind).toHaveLength(2);
    expect(wind.every((layer) => layer.props.visible && layer.props.opacity === 0.8)).toBe(true);
    states["model-field"] = { visible: false, opacity: 0.8 };
    states.isobars = { visible: true, opacity: 0.6 };
    const reordered = buildDeckLayers(LAYER_REGISTRY, states, context, ["isobars", "model-field"]);
    expect(reordered.find(({ id }) => id === ISOBARS_LAYER_ID)?.props).toMatchObject({ visible: true, opacity: 0.6 });
    expect(reordered.find(({ id }) => id === CONTOURS_LAYER_ID)?.props.visible).toBe(false);
    const hiddenWind = reordered.filter((layer): layer is IconLayer<WindBarb> => layer instanceof IconLayer);
    expect(hiddenWind).toHaveLength(2);
    expect(hiddenWind.every((layer) => !layer.props.visible)).toBe(true);
    expect(reordered.findIndex(({ id }) => id === ISOBARS_LAYER_ID)).toBeLessThan(reordered.findIndex(({ id }) => id === CONTOURS_LAYER_ID));
  });
});

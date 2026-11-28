// @vitest-environment jsdom
import type { Layer } from "@deck.gl/core";
import { PathLayer, TextLayer } from "@deck.gl/layers";
import { describe, expect, it } from "vitest";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import type { PressureCenter, ThicknessContours } from "../../types/contours";
import type { ContourLines } from "../../types/map";
import { readCanvasTokens } from "../canvasTokens";
import { buildContourLayers, CONTOURS_LAYER_ID } from "./ContourLayer";
import { LAYER_REGISTRY } from "./descriptors";
import { ISOBARS_LAYER_ID } from "./IsobarLayer";
import { buildDeckLayers, defaultLayerStates, NO_SCENE_DATA, type LayerBuildContext, type LayerState } from "./registry";
import { WIND_BARBS_LAYER_ID } from "./WindBarbLayer";

const read = cssTokenReader(tokensCss);
const pressure: ContourLines = {
  product: "mslp", runTime: "2026-11-06T00:00:00Z", forecastHour: 6,
  unit: "hPa", interval: 4, wind: null,
  segments: [[[-100, 35], [-99, 36]], [[-99, 36], [-98, 36.5]]],
};
const ctx: LayerBuildContext = {
  ...NO_SCENE_DATA, isobars: null, stations: [], labels: null, selected: null,
  tokens: readCanvasTokens(read), labelFont: read("--font-mono"), placeLabels: [], span: 360,
  basemap: { visible: true, opacity: 1 }, weatherShown: false,
};
const alwaysOn: LayerState = { visible: true, opacity: 1 };

const textById = (layers: readonly Layer[], id: string) =>
  layers.find((layer): layer is TextLayer => layer instanceof TextLayer && layer.id === id);
const pathById = (layers: readonly Layer[], id: string) =>
  layers.find((layer): layer is PathLayer => layer instanceof PathLayer && layer.id === id);

const contourSpec = { interval: 4, label: "hPa", colour: "#101418", width: 1.2 };
const sampleCenter = { lon: -98, lat: 35, kind: "L", value: 1002 } as const satisfies PressureCenter;
const thicknessFixture: ThicknessContours = {
  unit: "dam", interval: 6,
  paths: [{ value: 546, points: [[-101, 36], [-90, 36]] }],
  labels: [{ position: [-95, 36], value: 546 }],
};
const screenLayout = () => ({
  width: 440, height: 320, occupied: [],
  project: ([lon, lat]: readonly [number, number]) => ({ x: (lon + 104) * 20, y: (40 - lat) * 20 + 80 }),
});

describe("MSLP overlays over any selected field", () => {
  it("gives a center's 1002 reading room and lets the next native 1002 anchor use the available space", () => {
    const labels = [{ position: [-98, 36.2], value: 1002 }, { position: [-95, 36.2], value: 1002 }] as const;
    const contours: ContourLines = { ...pressure, product: "prate-ptype", interval: 2, labels, centers: [sampleCenter] };
    const layout = screenLayout();
    const context = { ...ctx, contours, contourSpec, outlookLabelLayout: layout };
    const states = defaultLayerStates();
    states["model-field"] = { visible: true, opacity: 0.8 };
    const layers = buildDeckLayers(LAYER_REGISTRY, states, context);
    expect(textById(layers, `${CONTOURS_LAYER_ID}-center-values-labels`)?.props.data).toBe(contours.centers);
    expect(textById(layers, `${CONTOURS_LAYER_ID}-labels`)?.props.data).toEqual([labels[1]]);
    expect(pathById(layers, CONTOURS_LAYER_ID)?.props.data).toBe(contours.segments);
    expect(layout.occupied).toEqual([]);
  });

  it.each([["model-field", "isobars"], ["isobars", "model-field"]] as const)(
    "keeps independent hPa labels readable over composite thickness in layer order %s, %s", (first, second) => {
      const pressureLabels = [{ position: [-95, 36], value: 1004 }] as const;
      const thickness: ThicknessContours = { ...thicknessFixture, labels: [
        { position: [-95, 36], value: 546 }, { position: [-98, 36.2], value: 540 },
        { position: [-92, 36], value: 546 }, { position: [-90, 36], value: 540 },
      ] };
      const context = { ...ctx, contourSpec, outlookLabelLayout: screenLayout(),
        contours: { ...pressure, product: "refd-1km-ptype", thickness },
        isobars: { ...pressure, labels: pressureLabels, centers: [sampleCenter] },
      };
      const states = defaultLayerStates();
      states["model-field"] = { visible: true, opacity: 0.8 };
      states.isobars = { visible: true, opacity: 0.6 };
      const layers = buildDeckLayers(LAYER_REGISTRY, states, context, [first, second]);
      expect(textById(layers, "isobars-labels")?.props).toMatchObject({ data: pressureLabels, getColor: ctx.tokens.colours.isobarLabel });
      expect(textById(layers, "isobars-center-values-labels")?.props.data).toEqual([sampleCenter]);
      const text = textById(layers, `${CONTOURS_LAYER_ID}-thickness-labels`);
      const labels = thickness.labels.slice(2);
      expect(text?.props.data).toEqual(labels);
      const colourOf = text?.props.getColor;
      if (typeof colourOf !== "function") throw new Error("Expected a thickness colour accessor");
      expect(labels.map((label, index) => colourOf(label, { index, data: labels, target: [] })))
        .toEqual([ctx.tokens.colours.thicknessWarm, ctx.tokens.colours.thicknessCold]);
      const contourStrokes = [CONTOURS_LAYER_ID + "-thickness-warm", ISOBARS_LAYER_ID];
      expect(layers.filter(({ id }) => contourStrokes.includes(id)).map(({ id }) => id))
        .toEqual(first === "model-field" ? contourStrokes : contourStrokes.reverse());
    },
  );

  it.each([
    { transparent: "isobars", owner: CONTOURS_LAYER_ID },
    { transparent: "model-field", owner: ISOBARS_LAYER_ID },
  ] as const)("keeps the other pressure layer complete and readable when $transparent is at 0%", ({ transparent, owner }) => {
    const compositeCenter = { ...sampleCenter, lon: -90 };
    const labels = [{ position: [-98, 36.2], value: 1004 }] as const;
    const context = { ...ctx, contourSpec, outlookLabelLayout: screenLayout(),
      isobars: { ...pressure, labels: [{ position: [-94, 32] as const, value: 1012 }], centers: [sampleCenter], thickness: thicknessFixture },
      contours: { ...pressure, product: "prate-ptype", labels, centers: [compositeCenter], thickness: thicknessFixture },
    };
    const states = defaultLayerStates();
    states["model-field"] = { visible: true, opacity: 0.8 };
    states.isobars = { visible: true, opacity: 0.6 };
    states[transparent].opacity = 0;
    const layers = buildDeckLayers(LAYER_REGISTRY, states, context).filter(({ props }) => props.visible && props.opacity > 0);
    expect(pathById(layers, owner)).toBeDefined();
    expect(textById(layers, `${owner}-labels`)?.props.data).toEqual(transparent === "isobars" ? labels : context.isobars.labels);
    expect(textById(layers, `${owner}-center-values-labels`)).toBeDefined();
    expect(pathById(layers, `${owner}-thickness-warm`)?.props.data).toBeDefined();
    expect(textById(layers, `${owner}-thickness-labels`)?.props.data).toEqual(thicknessFixture.labels);
  });

  it("uses independent thickness when the visible composite has an empty level set", () => {
    const context = { ...ctx, contourSpec,
      contours: { ...pressure, product: "prate-ptype", thickness: { ...thicknessFixture, paths: [], labels: [] } },
      isobars: { ...pressure, thickness: thicknessFixture },
    };
    const states = defaultLayerStates();
    states["model-field"] = { visible: true, opacity: 0.8 };
    states.isobars = { visible: true, opacity: 0.6 };
    const layers = buildDeckLayers(LAYER_REGISTRY, states, context);
    expect(pathById(layers, "isobars-thickness-warm")?.props.opacity).toBe(0.6);
    expect(textById(layers, "isobars-thickness-labels")?.props.data).toBe(thicknessFixture.labels);
  });

  it.each([
    { withoutBuild: "isobars", owner: CONTOURS_LAYER_ID },
    { withoutBuild: "model-field", owner: ISOBARS_LAYER_ID },
  ] as const)("keeps pressure geometry on the drawable layer when $withoutBuild has no builder", ({ withoutBuild, owner }) => {
    const descriptors = LAYER_REGISTRY.map((descriptor) => {
      if (descriptor.id !== withoutBuild) return descriptor;
      const withoutBuilder = { ...descriptor };
      delete withoutBuilder.build;
      return withoutBuilder;
    });
    const isobars = { ...pressure, thickness: thicknessFixture };
    const context = { ...ctx, contourSpec, isobars, contours: { ...isobars, product: "refd-1km-ptype" } };
    const layers = buildDeckLayers(descriptors, { isobars: alwaysOn, "model-field": alwaysOn }, context);
    expect(pathById(layers, owner)?.props.data).toBe(pressure.segments);
    expect(pathById(layers, `${owner}-thickness-warm`)).toBeDefined();
    expect(textById(layers, `${owner}-thickness-labels`)?.props.data).toBe(thicknessFixture.labels);
  });

  it("hands a shared pressure and wind frame to the independent layer only while it draws", () => {
    const frame = { ...pressure, centers: [sampleCenter], thickness: thicknessFixture };
    const context: LayerBuildContext = { ...ctx, contourSpec, contours: frame, isobars: frame,
      windBarbs: [{ position: [-96, 34], direction: 270, speed: 15 }],
    };
    const states = defaultLayerStates();
    states["model-field"] = { visible: true, opacity: 0.8 };
    states.isobars = { visible: true, opacity: 0.6 };
    const layers = buildDeckLayers(LAYER_REGISTRY, states, context);
    expect(pathById(layers, "isobars-thickness-warm")).toBeDefined();
    expect(pathById(layers, CONTOURS_LAYER_ID)).toBeUndefined();
    expect(pathById(layers, `${CONTOURS_LAYER_ID}-thickness-warm`)).toBeUndefined();
    states.isobars.opacity = 0;
    const fallback = buildDeckLayers(LAYER_REGISTRY, states, context);
    expect(pathById(fallback, CONTOURS_LAYER_ID)?.props.opacity).toBe(0.8);
    expect(pathById(fallback, `${CONTOURS_LAYER_ID}-thickness-warm`)?.props.opacity).toBe(0.8);
    for (const drawn of [layers, fallback]) {
      expect(drawn.find(({ id }) => id === WIND_BARBS_LAYER_ID)?.props)
        .toMatchObject({ data: context.windBarbs, visible: true, opacity: 0.8 });
    }
  });

  it("suppresses the composite MSLP lines, labels, and centers while the dashed thickness still renders", () => {
    const centers: readonly PressureCenter[] = [
      { lon: -98.5, lat: 36.5, kind: "H", value: 1035 },
      { lon: -95.5, lat: 33.5, kind: "L", value: 999 },
    ];
    const thickness: ThicknessContours = {
      unit: "dam", interval: 6,
      paths: [
        { value: 522, points: [[-101, 33], [-96, 33]] },
        { value: 546, points: [[-101, 39], [-96, 39]] },
      ],
      labels: [{ position: [-98.5, 33], value: 522 }],
    };
    const mslpPaths = [[[-101, 31], [-95, 33], [-90, 35]]] as const;
    const labels = [{ position: [-97, 33], value: 1004 }] as const;
    const composite: ContourLines = {
      ...pressure, product: "prate-ptype", segments: [], paths: mslpPaths, labels, centers, thickness,
    };
    const context: LayerBuildContext = {
      ...ctx,
      isobars: { ...pressure, paths: mslpPaths, segments: [], labels, centers, thickness },
      contours: composite,
      contourSpec,
    };
    const standalone = { ...context, isobars: null };
    const modelState = { visible: true, opacity: 0.8 };
    expect(buildContourLayers(standalone, modelState).slice(-3).map(({ id }) => id)).toEqual([
      `${CONTOURS_LAYER_ID}-thickness-labels`, `${CONTOURS_LAYER_ID}-centers-labels`, `${CONTOURS_LAYER_ID}-center-values-labels`,
    ]);
    const states = defaultLayerStates();
    states["model-field"] = { visible: true, opacity: 0.8 };
    states.isobars = { visible: true, opacity: 0.6 };
    const layers = buildDeckLayers(LAYER_REGISTRY, states, context);
    const ids = layers.map(({ id }) => id);
    // The composite's duplicate MSLP level set, and the overlay's copy of the thickness, are gone.
    for (const id of [CONTOURS_LAYER_ID, `${CONTOURS_LAYER_ID}-halo`, `${CONTOURS_LAYER_ID}-labels`,
      `${CONTOURS_LAYER_ID}-centers-labels`, `${CONTOURS_LAYER_ID}-center-values-labels`,
      "isobars-thickness-cold", "isobars-thickness-warm", "isobars-thickness-labels"]) {
      expect(ids).not.toContain(id);
    }
    // The dashed thickness still renders from the composite, hoisted above every fill.
    expect(ids).toContain(`${CONTOURS_LAYER_ID}-thickness-cold`);
    expect(ids).toContain(`${CONTOURS_LAYER_ID}-thickness-warm`);
    expect(ids.indexOf(`${CONTOURS_LAYER_ID}-thickness-labels`)).toBeGreaterThan(ids.indexOf(`${CONTOURS_LAYER_ID}-thickness-cold`));
    // The independent overlay keeps its own complete MSLP set, centers included.
    for (const id of [ISOBARS_LAYER_ID, "isobars-halo", "isobars-labels", "isobars-centers-labels", "isobars-center-values-labels"]) {
      expect(ids).toContain(id);
    }
  });
});

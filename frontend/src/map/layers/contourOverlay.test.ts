// @vitest-environment jsdom
import type { Layer } from "@deck.gl/core";
import { PathLayer, TextLayer } from "@deck.gl/layers";
import { describe, expect, it } from "vitest";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import type { PressureCenter, ThicknessContours } from "../../types/contours";
import type { ContourLines } from "../../types/map";
import { readCanvasTokens } from "../canvasTokens";
import { buildContourLayers, buildPressureCenters, buildThicknessContours, CONTOURS_LAYER_ID, dashPaths } from "./ContourLayer";
import { buildIsobarLayers, ISOBARS_LAYER_ID } from "./IsobarLayer";
import { NO_SCENE_DATA, type LayerBuildContext, type LayerState } from "./registry";

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

const thicknessFixture: ThicknessContours = {
  unit: "dam", interval: 6,
  paths: [{ value: 546, points: [[-101, 36], [-90, 36]] }],
  labels: [{ position: [-95, 36], value: 546 }],
};

describe("MSLP overlays over any selected field", () => {
  it.each([ISOBARS_LAYER_ID, CONTOURS_LAYER_ID])("draws bold pressure markers with a separate reading above in %s", (id) => {
    const centers = [
      { lon: -98.5, lat: 36.5, kind: "H", value: 1035 },
      { lon: -95.5, lat: 33.5, kind: "L", value: 999 },
    ] as const satisfies readonly PressureCenter[];
    const lines = { ...pressure, centers };
    const state = { visible: true, opacity: 0.9 };
    const layers = id === ISOBARS_LAYER_ID
      ? buildIsobarLayers({ ...ctx, isobars: lines }, state)
      : buildContourLayers({ ...ctx, contours: lines,
        contourSpec: { interval: 4, label: "hPa", colour: "#101418", width: 1 } }, state);
    const glyphs = textById(layers, `${id}-centers-labels`);
    const values = textById(layers, `${id}-center-values-labels`);
    if (glyphs === undefined || values === undefined) throw new Error("Expected both center label layers");
    for (const layer of [glyphs, values]) {
      expect(layer.props).toMatchObject({ data: centers, visible: true, opacity: 0.9, pickable: false,
        sizeUnits: "pixels", getTextAnchor: "middle", outlineColor: ctx.tokens.colours.labelHalo,
        outlineWidth: 5, fontSettings: { sdf: true, fontSize: 64 } });
    }
    expect(glyphs.props).toMatchObject({ getSize: 30, getPixelOffset: [0, 0],
      fontWeight: 800, fontFamily: ctx.tokens.stationLabelFont });
    expect(values.props).toMatchObject({ getSize: 16, getPixelOffset: [0, -24],
      fontWeight: 700, fontFamily: ctx.labelFont, getColor: ctx.tokens.colours.labelText });
    const { getText: glyphText, getColor: glyphColour, getPosition: glyphPosition } = glyphs.props;
    const { getText: valueText, getPosition: valuePosition } = values.props;
    if (typeof glyphText !== "function" || typeof glyphColour !== "function" || typeof glyphPosition !== "function"
      || typeof valueText !== "function" || typeof valuePosition !== "function") throw new Error("Expected center accessors");
    for (const [index, center] of centers.entries()) {
      const info = { index, data: centers, target: [] };
      expect(glyphText(center, info)).toBe(center.kind);
      expect(glyphColour(center, info)).toEqual(center.kind === "H" ? ctx.tokens.colours.centerHigh : ctx.tokens.colours.centerLow);
      expect(glyphPosition(center, info)).toEqual([center.lon, center.lat]);
      expect(valueText(center, info)).toBe(String(center.value));
      expect(valuePosition(center, info)).toEqual([center.lon, center.lat]);
    }
  });

  it("omits markers for pressure data without centers", () => {
    expect(buildIsobarLayers({ ...ctx, isobars: pressure }, alwaysOn).map(({ id }) => id)).toEqual(["isobars-halo", ISOBARS_LAYER_ID]);
    expect(buildPressureCenters(ISOBARS_LAYER_ID, [], ctx, alwaysOn)).toEqual([]);
  });

  it("draws the cold and warm halves of the thickness level set as dashed lines with rounded dam labels", () => {
    const thickness: ThicknessContours = {
      unit: "dam", interval: 6,
      paths: [
        // A straight 5-degree cold contour, the 540 boundary, and a straight warm one.
        { value: 522, points: [[-101, 33], [-96, 33]] },
        { value: 540, points: [[-101, 34], [-96, 34]] },
        { value: 546, points: [[-101, 39], [-96, 39]] },
      ],
      labels: [
        { position: [-98.5, 33], value: 522 },
        { position: [-98.5, 34], value: 540 },
        { position: [-98.5, 39], value: 546 },
      ],
    };
    const layers = buildThicknessContours("mslp", thickness, ctx, { visible: true, opacity: 0.75 }, 45);
    expect(layers.map(({ id }) => id)).toEqual(["mslp-thickness-cold", "mslp-thickness-warm", "mslp-thickness-labels"]);
    const cold = pathById(layers, "mslp-thickness-cold");
    const warm = pathById(layers, "mslp-thickness-warm");
    const text = textById(layers, "mslp-thickness-labels");
    if (cold === undefined || warm === undefined || text === undefined) throw new Error("Expected dashed thickness layers");
    const coldDashes = cold.props.data as readonly (readonly (readonly [number, number])[])[];
    const warmDashes = warm.props.data as readonly (readonly (readonly [number, number])[])[];
    expect(coldDashes.length).toBeGreaterThan(4);
    expect(warmDashes.length).toBeGreaterThan(4);
    for (const dash of [...coldDashes, ...warmDashes]) {
      expect(dash.length).toBeGreaterThanOrEqual(2);
    }
    expect(coldDashes[0]?.[0]).toEqual([-101, 33]);
    expect(warmDashes[0]?.[0]).toEqual([-101, 39]);
    for (const line of [cold, warm]) {
      expect(line.props).toMatchObject({ visible: true, opacity: 0.75, pickable: false, widthUnits: "pixels",
        getWidth: 1.5, antialiasing: true, capRounded: true, jointRounded: true });
    }
    expect(cold.props.getColor).toEqual(ctx.tokens.colours.thicknessCold);
    expect(warm.props.getColor).toEqual(ctx.tokens.colours.thicknessWarm);
    expect(text.props).toMatchObject({ data: thickness.labels, visible: true, opacity: 0.75, pickable: false, sizeUnits: "pixels",
      getSize: ctx.tokens.lengths.labelSize, outlineColor: ctx.tokens.colours.labelHalo,
      outlineWidth: ctx.tokens.lengths.labelHaloWidth, fontFamily: ctx.labelFont });
    const info = { index: 0, data: thickness.labels, target: [] };
    const textOf = text.props.getText;
    const colourOf = text.props.getColor;
    if (typeof textOf !== "function" || typeof colourOf !== "function") throw new Error("Expected thickness label accessors");
    expect(textOf(thickness.labels[0] as ContourLabelDatum, info)).toBe("522");
    expect(textOf(thickness.labels[1] as ContourLabelDatum, info)).toBe("540");
    // 540 dam is the cold boundary: labels at or below it read blue, above it red.
    expect(colourOf(thickness.labels[0] as ContourLabelDatum, info)).toEqual(ctx.tokens.colours.thicknessCold);
    expect(colourOf(thickness.labels[1] as ContourLabelDatum, info)).toEqual(ctx.tokens.colours.thicknessCold);
    expect(colourOf(thickness.labels[2] as ContourLabelDatum, info)).toEqual(ctx.tokens.colours.thicknessWarm);
  });

  it("memoizes dash pieces per path set and span bucket", () => {
    const paths = [[[-101, 33], [-96, 33]], [[-101, 39], [-96, 39]]] as const;
    const dashes = dashPaths(paths, 45);
    expect(dashes.length).toBeGreaterThan(4);
    expect(dashPaths(paths, 45)).toBe(dashes);
    // Spans inside one bucket share the memoized geometry; other buckets rebuild it.
    expect(dashPaths(paths, 46)).toBe(dashes);
    expect(dashPaths(paths, 40)).not.toBe(dashes);
    expect(dashPaths(paths, 40).every((dash) => dash.length >= 2)).toBe(true);
  });

  it("invalidates the cold and warm thickness label fills when the theme changes", () => {
    const dark = ctx.tokens.colours;
    const light = {
      ...dark, thicknessCold: [29, 78, 216, 255], thicknessWarm: [220, 38, 38, 255],
    } as const;
    const darkLabel = textById(buildThicknessContours("mslp", thicknessFixture, ctx, alwaysOn, 45), "mslp-thickness-labels");
    const lightLabel = textById(buildThicknessContours("mslp", thicknessFixture,
      { ...ctx, tokens: { ...ctx.tokens, colours: light } }, alwaysOn, 45), "mslp-thickness-labels");
    expect(darkLabel?.props.data).toBe(thicknessFixture.labels);
    expect(lightLabel?.props.data).toBe(thicknessFixture.labels);
    expect(darkLabel?.props.updateTriggers.getColor).toEqual([dark.thicknessCold, dark.thicknessWarm]);
    expect(lightLabel?.props.updateTriggers.getColor).toEqual([light.thicknessCold, light.thicknessWarm]);
  });
});

type ContourLabelDatum = { position: readonly [number, number]; value: number };

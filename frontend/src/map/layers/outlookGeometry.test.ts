import { describe, expect, it } from "vitest";
import tokensCss from "../../styles/tokens.css?raw";
import { cssTokenReader } from "../../test/cssTokens";
import { readCanvasTokens } from "../canvasTokens";
import type { Outlook } from "../../types/map";
import { NO_SCENE_DATA, type LayerBuildContext } from "./registry";
import { buildOutlookLayers, outlookShapes, type AreaFill } from "./SPCOutlookLayer";
import { closedPaths, containsPoint, labelPoint, type Ring } from "./outlookGeometry";

const square: Ring = [
  [0, 0],
  [10, 0],
  [10, 10],
  [0, 10],
];
const hole: Ring = [
  [4, 4],
  [6, 4],
  [6, 6],
  [4, 6],
];
/** A U open to the north: the middle of its box is outside it. */
const notched: Ring = [
  [0, 0],
  [10, 0],
  [10, 10],
  [7, 10],
  [7, 3],
  [3, 3],
  [3, 10],
  [0, 10],
];

describe("outlook geometry", () => {
  it("closes each ring for the outline", () => {
    const [path] = closedPaths([square]);
    expect(path).toHaveLength(5);
    expect(path?.[4]).toEqual([0, 0]);
  });

  it("tells inside from outside, holes and notches included", () => {
    expect(containsPoint([square], [5, 5])).toBe(true);
    expect(containsPoint([square, hole], [5, 5])).toBe(false);
    expect(containsPoint([square, hole], [2, 5])).toBe(true);
    expect(containsPoint([notched], [5, 6])).toBe(false);
    expect(containsPoint([notched], [5, 1])).toBe(true);
    expect(containsPoint([square], [11, 5])).toBe(false);
  });

  it("puts the label inside convex, holed, and concave areas", () => {
    expect(labelPoint([square])).toEqual([5, 5]);
    expect(labelPoint([square, hole])).toEqual([2, 5]);
    expect(labelPoint([notched])).toEqual([1.5, 5]);
    expect(labelPoint([[[1, 2]]])).toEqual([1, 2]);
  });

  it("keeps alternate badge positions inside concave and holed areas", () => {
    for (const rings of [[square], [square, hole], [notched]]) {
      for (const fraction of [0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9]) {
        expect(containsPoint(rings, labelPoint(rings, fraction))).toBe(true);
      }
    }
  });

  it("keeps CIG levels distinct, preserves holes, and reuses geometry across zoom and opacity", () => {
    const outlook: Outlook = {
      title: "", subtitle: "", validFrom: null, validTo: null, issued: null, covers: true, sourceUrl: "", attribution: "",
      areas: [1, 2, 3, 0].map((level) => ({ label: level === 0 ? "SIGN" : `CIG${String(level)}`, description: "", fill: "#888888", stroke: "#000000", rank: level,
        hatch: true, hatchLevel: level, rings: [square, hole] })),
    };
    const shapes = outlookShapes(outlook);
    expect(shapes.fills).toHaveLength(0);
    expect(shapes.hatches.map((group) => group.map((fill) => fill.area.hatchLevel))).toEqual([[1], [0, 2], [3]]);
    const context: LayerBuildContext = { ...NO_SCENE_DATA, stations: [], labels: null, selected: null,
      tokens: readCanvasTokens(cssTokenReader(tokensCss)), labelFont: "sans-serif", placeLabels: [],
      span: 80, basemap: { visible: true, opacity: 1 }, weatherShown: true, outlooks: { wind: outlook } };
    const first = buildOutlookLayers("wind", context, { visible: true, opacity: 1 });
    const zoomed = buildOutlookLayers("wind", { ...context, span: 2 }, { visible: false, opacity: 0.4 });
    for (const [i, layer] of first.filter((item) => item.id.includes("-hatch-")).entries()) {
      const data = layer.props.data as AreaFill[];
      expect(data.every((fill) => fill.polygon.length === 2)).toBe(true);
      expect(zoomed.find((item) => item.id === layer.id)?.props.data).toBe(data);
      expect(zoomed.find((item) => item.id === layer.id)?.props.opacity).toBe(0.4);
      const extension = layer.props.extensions[0];
      if (extension === undefined) throw new Error("Missing CIG pattern");
      // Deck invokes shader hooks with the primitive layer as `this`.
      const shaders = extension.getShaders.call(layer, extension) as { defines: { CIG_HATCH_LEVEL: number } };
      expect(shaders.defines.CIG_HATCH_LEVEL).toBe(i + 1);
    }
  });
});

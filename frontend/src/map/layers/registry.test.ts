// @vitest-environment jsdom
import { BitmapLayer, IconLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { afterEach, describe, expect, it, vi } from "vitest";
import tokensCss from "../../styles/tokens.css?raw";
import surfaceFixture from "@contracts/public/map/surface.json";
import fieldFixture from "@contracts/public/map/field.shrf?url&inline";
import { parseFieldFrame } from "../../lib/contracts/frames";
import { parseSurfacePlots } from "../../lib/contracts/surface";
import { buildSurfacePlotLayers } from "./SurfacePlotLayer";
import { buildWindBarbLayers } from "./WindBarbLayer";
import { containsPoint } from "./outlookGeometry";
import { cssTokenReader } from "../../test/cssTokens";
import type { Station } from "../../types/contracts";
import type { Outlook } from "../../types/map";
import { readCanvasTokens } from "../canvasTokens";
import { LAYER_REGISTRY } from "./descriptors";
import {
  buildDeckLayers,
  clampOpacity,
  controllableLayers,
  defaultLayerStates,
  LAYER_IDS,
  moveInOrder,
  NO_SCENE_DATA,
  type LayerBuildContext,
  type LayerDescriptor,
} from "./registry";

const read = cssTokenReader(tokensCss);
const station: Station = {
  id: "72357",
  name: "Norman/Max Westheimer A, Ok., Usa",
  latitude: 35.18,
  longitude: -97.44,
  source: "FM35",
  availability: "unknown",
};
const ctx: LayerBuildContext = {
  ...NO_SCENE_DATA,
  stations: [station],
  labels: { stations: [station], tier: "id" },
  selected: null,
  tokens: readCanvasTokens(read),
  labelFont: read("--font-mono"),
  placeLabels: [],
  span: 360,
  basemap: { visible: true, opacity: 1 },
  weatherShown: false,
};

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

describe("layer registry", () => {
  it.each([["", true], ["", false], ["flag", true]] as const)("keeps flags sharp and reconstructs other fields only in Smooth: units=%s, stepped=%s", (units, stepped) => {
    vi.stubGlobal("ImageData", class { constructor(public data: Uint8ClampedArray, public width: number, public height: number) {} });
    const image: ImageBitmap = { width: 2, height: 2, close: vi.fn() };
    const raster = { image, bounds: { west: -100, south: 30, east: -90, north: 40 } };
    const frame = { ...parseFieldFrame(Uint8Array.from(atob(fieldFixture.split(",")[1] ?? ""), (c) => c.charCodeAt(0)).buffer),
      width: 2, height: 2, cells: new Uint16Array([0, 255, 256, 65535]), nodata: 65535 };
    const palette = { display: "auto" as const, stops: [[-0.3, "#000000"], [0.01, "#f0f0f0"], [15, "#893d48"]] as const, units, stepped, floor: null, ceiling: null, ticks: [] };
    const context = { ...ctx, field: { ...raster, frame, palette }, radar: raster, satellite: raster };
    const sharpImage = vi.fn(() => image);
    Object.defineProperty(context.field, "image", { get: sharpImage });
    const states = defaultLayerStates();
    let smoothImage: unknown;
    let paletteImage: unknown;
    for (const smoothing of [true, undefined, false, true]) {
      const reconstruct = smoothing && units !== "flag";
      sharpImage.mockClear();
      states["model-field"] = { visible: true, opacity: 0.7, ...(smoothing === undefined ? {} : { smoothing }) };
      const layers = buildDeckLayers(LAYER_REGISTRY, states, context).filter((layer): layer is BitmapLayer => layer instanceof BitmapLayer);
      expect(layers.map((layer) => layer.id)).toEqual([reconstruct ? "model-field-smooth" : "model-field", "satellite", "radar"]);
      for (const layer of layers) {
        expect(layer.props.textureParameters).toEqual({ minFilter: "nearest", magFilter: "nearest", mipmapFilter: "none" });
        if (layer.id === "model-field-smooth") {
          const encoded = layer.props.image as ImageData;
          expect([...encoded.data]).toEqual([0, 0, 255, 255, 0, 255, 255, 255, 1, 0, 255, 255, 255, 255, 0, 255]);
          if (smoothImage !== undefined) expect(encoded).toBe(smoothImage);
          smoothImage = encoded;
          const colours = (layer.props as typeof layer.props & { fieldPalette: ImageData }).fieldPalette;
          if (paletteImage !== undefined) expect(colours).toBe(paletteImage);
          paletteImage = colours;
          expect(colours.height).toBe(stepped ? 3 : 1);
          if (stepped) {
            expect(colours.width).toBe(palette.stops.length);
            expect([...colours.data.slice(0, 12)]).toEqual([0, 0, 0, 255, 240, 240, 240, 255, 137, 61, 72, 255]);
            const edge = new DataView(new ArrayBuffer(4));
            for (const [i, [value]] of palette.stops.entries()) {
              const offset = (colours.width + i) * 4;
              for (let byte = 0; byte < 3; byte++) edge.setUint8(byte, colours.data[offset + byte] ?? 0);
              edge.setUint8(3, colours.data[(colours.width * 2 + i) * 4] ?? 0);
              expect(edge.getFloat32(0, true)).toBe(Math.fround(value));
              expect(colours.data[offset + 3]).toBe(255);
              expect(colours.data[(colours.width * 2 + i) * 4 + 3]).toBe(255);
            }
          }
        } else {
          expect(layer.props.image).toBe(image);
        }
      }
      expect(layers[0]?.props.visible).toBe(true);
      expect(layers[0]?.props.opacity).toBe(0.7);
      expect(sharpImage).toHaveBeenCalledTimes(reconstruct ? 0 : 1);
      const blend = buildDeckLayers(LAYER_REGISTRY, states, { ...context, previousField: context.field, fieldMix: 0.25 }).filter((layer): layer is BitmapLayer => layer instanceof BitmapLayer);
      expect(blend).toHaveLength(layers.length + 1);
      expect((blend[0]?.props.opacity ?? 0) * (1 - (blend[1]?.props.opacity ?? 0)) + (blend[1]?.props.opacity ?? 0)).toBeCloseTo(0.7);
    }
  });

  it("keeps surface glyph buffers on opacity changes and rebuilds when observations change", () => {
    const plots = parseSurfacePlots(surfaceFixture).plots;
    const context = { ...ctx, surfacePlots: plots };
    const initialLayers = buildSurfacePlotLayers(context, { visible: true, opacity: 1 });
    const dimmedLayers = buildSurfacePlotLayers(context, { visible: false, opacity: 0.4 });
    const first = initialLayers[0], dimmed = dimmedLayers[0];
    expect(first?.props.data).toBeTruthy();
    expect(dimmed?.props.data).toBe(first?.props.data);
    expect(dimmed?.props.visible).toBe(false);
    expect(dimmed?.props.opacity).toBe(0.4);
    expect(first).toBeInstanceOf(TextLayer);
    if (first === undefined) throw new Error("Expected surface labels");
    expect(first.props.outlineWidth).toBeGreaterThan(0);
    expect(first.props.outlineColor).toEqual(ctx.tokens.colours.labelHalo);
    expect(first.props.background).toBe(false);
    const wind = initialLayers[2];
    expect(wind).toBeInstanceOf(IconLayer);
    expect(wind?.props.data).toBe(dimmedLayers[2]?.props.data);
    expect(wind?.props.pickable).toBe(true);
    expect(wind?.props.id).toBe("surface-plots-wind");
    expect(initialLayers[1]?.props.pickable).toBe(false);
    const model = buildWindBarbLayers({ ...ctx, windBarbs: [{ position: [-97, 35], direction: 270, speed: 85 }] }, { visible: true, opacity: 1 });
    expect(model[1]?.props.iconAtlas).toBe((wind as IconLayer).props.iconAtlas);
    const getAngle = model[1]?.props.getAngle;
    expect(typeof getAngle === "function" && getAngle({ position: [-97, 35], direction: 270, speed: 85 }, { index: 0, data: [], target: [] })).toBe(-180);
    const refreshed = buildSurfacePlotLayers({ ...context, surfacePlots: [...plots] }, { visible: true, opacity: 1 })[0];
    expect(refreshed?.props.data).not.toBe(first.props.data);
  });

  it("builds the deck layers sorted by zOrder, layers without data left out", () => {
    const shuffled = [...LAYER_REGISTRY].reverse();
    const ids = buildDeckLayers(shuffled, defaultLayerStates(), ctx).map((layer) => layer.id);
    expect(ids).toEqual(["stations", "station-labels", "selection-halo", "selection-marker"]);
  });

  it("stacks weather fills in the user's order while readings stay above the stack", () => {
    vi.stubEnv("MODE", "fixture");
    const outlook = { title: "", subtitle: "", validFrom: null, validTo: null, issued: null, covers: true, sourceUrl: "", attribution: "", areas: [] };
    const withData: LayerBuildContext = { ...ctx, outlooks: { cat: outlook, hail: outlook }, domain: [[-100, 30], [-90, 30], [-90, 40]], weatherShown: true };
    const states = defaultLayerStates();
    states["spc-cat"].visible = true;
    const order = ["spc-cat", "model-field", "radar", "spc-torn", "spc-wind", "spc-hail"];
    const ids = buildDeckLayers(LAYER_REGISTRY, states, withData, order).map((layer) => layer.id);
    expect(ids.indexOf("spc-cat-fill")).toBeLessThan(ids.indexOf("spc-hail-fill"));
    expect(ids.indexOf("spc-hail-labels")).toBeGreaterThan(ids.indexOf("boundaries-states"));
    expect(ids.indexOf("boundaries-coastline")).toBeLessThan(ids.indexOf("model-domain"));
    expect(ids).not.toContain("boundaries-lakes");
    expect(ids.indexOf("model-domain")).toBeLessThan(ids.indexOf("stations"));
    const reversed = buildDeckLayers(LAYER_REGISTRY, states, withData, [...order].reverse()).map((layer) => layer.id);
    expect(reversed.indexOf("spc-hail-fill")).toBeLessThan(reversed.indexOf("spc-cat-fill"));
    expect(moveInOrder(["a", "b", "c"], "a", 1)).toEqual(["b", "a", "c"]);
    expect(moveInOrder(["a", "b", "c"], "c", 1)).toEqual(["a", "b", "c"]);
    expect(moveInOrder(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
  });

  it.each(["live", "production"])("keeps only ocean coastlines over weather alongside native political borders in %s maps", (mode) => {
    vi.stubEnv("MODE", mode);
    const layers = buildDeckLayers(LAYER_REGISTRY, defaultLayerStates(), { ...ctx, weatherShown: true });
    expect(layers.filter((layer) => layer.id.startsWith("boundaries-")).map((layer) => layer.id)).toEqual(["boundaries-coastline"]);
    expect(layers.some((layer) => layer.id === "stations")).toBe(true);
  });

  it("inserts weather fills below native outlines while labels and observations stay on top", () => {
    vi.stubEnv("MODE", "live");
    const outlook = { title: "", subtitle: "", validFrom: null, validTo: null, issued: null, covers: true, sourceUrl: "", attribution: "", areas: [] };
    const states = defaultLayerStates();
    const order = ["surface-plots", "spc-hail", "spc-cat"];
    const layers = buildDeckLayers(LAYER_REGISTRY, states, {
      ...ctx, surfacePlots: parseSurfacePlots(surfaceFixture).plots,
      outlooks: { cat: outlook, hail: outlook }, weatherBeforeId: "waterway",
    }, order);
    const fills = layers.filter((layer) => layer.id.startsWith("spc-") && !layer.id.endsWith("-labels"));
    expect(fills.length).toBeGreaterThan(0);
    expect(fills.every((layer) => (layer.props as { beforeId?: string }).beforeId === "waterway")).toBe(true);
    expect(layers[0]?.id).toBe("spc-hail-fill");
    expect(layers.find((layer) => layer.id === "stations")?.props).not.toHaveProperty("beforeId");
    for (const layer of layers.filter((item) => item.id.endsWith("-labels") || item.id.startsWith("surface-plots"))) {
      expect(layer.props).not.toHaveProperty("beforeId");
      expect(layers.indexOf(layer)).toBeGreaterThan(Math.max(...fills.map((fill) => layers.indexOf(fill))));
    }
  });

  it("separates the four outlook labels inside their areas and away from surface readings", () => {
    const outlook: Outlook = {
      title: "", subtitle: "", validFrom: null, validTo: null, issued: null, covers: true, sourceUrl: "", attribution: "",
      areas: [{ label: "5%", description: "", fill: "#ffff00", stroke: "#000000", rank: 1, hatch: false, hatchLevel: 0,
        rings: [[[0, 0], [10, 0], [10, 10], [0, 10]]] }],
    };
    const layout = { width: 600, height: 600, project: ([x, y]: readonly [number, number]) => ({ x: 50 + x * 50, y: 50 + y * 50 }),
      occupied: [{ x: 300, y: 300, width: 84, height: 64 }] };
    const context = { ...ctx, outlooks: { cat: outlook, torn: outlook, wind: outlook, hail: outlook }, outlookLabelLayout: layout };
    const states = defaultLayerStates();
    for (const key of ["spc-cat", "spc-torn", "spc-wind", "spc-hail"] as const) states[key].visible = true;
    const labels = buildDeckLayers(LAYER_REGISTRY, states, context).filter((layer): layer is TextLayer => layer instanceof TextLayer && layer.id.startsWith("spc-") && layer.id.endsWith("-labels"));
    expect(labels).toHaveLength(4);
    const positions = labels.flatMap((layer) => Array.from(layer.props.data as { position: readonly [number, number] }[]));
    expect(positions).toHaveLength(4);
    expect(new Set(positions.map(({ position }) => position.join()))).toHaveLength(4);
    for (const { position } of positions) {
      expect(containsPoint(outlook.areas[0]?.rings ?? [], position)).toBe(true);
      expect(Math.abs(layout.project(position).y - 300)).toBeGreaterThan(43);
    }
    for (const layer of labels) {
      expect(layer.props.background).toBe(false);
      expect(layer.props.outlineWidth).toBeGreaterThan(0);
    }
    for (const [i, a] of positions.entries()) {
      for (const b of positions.slice(i + 1)) {
        expect(Math.abs(layout.project(a.position).y - layout.project(b.position).y)).toBeGreaterThanOrEqual(22);
      }
    }
    expect(layout.occupied).toHaveLength(1);
    const rebuilt = buildDeckLayers(LAYER_REGISTRY, states, context).filter((layer) => layer.id.startsWith("spc-") && layer.id.endsWith("-labels"));
    expect(rebuilt.map((layer) => layer.props.data)).toEqual(labels.map((layer) => layer.props.data));
  });

  it("passes visibility and opacity to the layers as props", () => {
    const states = defaultLayerStates();
    states.stations = { visible: false, opacity: 0.4 };
    const layers = buildDeckLayers(LAYER_REGISTRY, states, ctx);
    const stations = layers.find((layer) => layer.id === "stations");
    expect(stations?.props.visible).toBe(false);
    expect(stations?.props.opacity).toBe(0.4);
    // The data stays the same array, so deck.gl keeps the buffers on a toggle.
    expect(stations?.props.data).toBe(ctx.stations);
  });

  it("clamps out-of-range opacity before building", () => {
    const states = defaultLayerStates();
    states.stations = { visible: true, opacity: 7 };
    const stations = buildDeckLayers(LAYER_REGISTRY, states, ctx).find((layer) => layer.id === "stations");
    expect(stations?.props.opacity).toBe(1);
    expect(clampOpacity(-1)).toBe(0);
    expect(clampOpacity(Number.NaN)).toBe(0);
  });

  it("renders a test-only descriptor with no WeatherMap change", () => {
    const probe: LayerDescriptor<"probe-layer"> = {
      id: "probe-layer",
      label: "Probe",
      kind: "deck",
      zOrder: 150,
      userControllable: true,
      opacityControl: true,
      defaults: { visible: true, opacity: 0.5 },
      build: (context, state) => [
        new ScatterplotLayer<Station>({
          id: "probe-layer",
          data: context.stations,
          getPosition: (s) => [s.longitude, s.latitude],
          visible: state.visible,
          opacity: state.opacity,
        }),
      ],
    };
    const registry: LayerDescriptor<string>[] = [...LAYER_REGISTRY, probe];
    const layers = buildDeckLayers(registry, defaultLayerStates(), ctx);
    expect(layers.map((layer) => layer.id)).toEqual([
      "stations",
      "station-labels",
      "probe-layer",
      "selection-halo",
      "selection-marker",
    ]);
    expect(layers.find((layer) => layer.id === "probe-layer")?.props.opacity).toBe(0.5);
    expect(controllableLayers(registry).map((d) => d.id)).toContain("probe-layer");
  });

  it("lists only the user-controllable layers for the panel, in zOrder", () => {
    expect(controllableLayers(LAYER_REGISTRY).map((d) => d.id)).toEqual([
      "basemap", "model-field", "satellite", "radar", "storm-reports", "spc-cat", "spc-torn", "spc-wind", "spc-hail", "surface-plots", "model-domain", "place-labels",
      "stations", "station-labels",
    ]);
  });

  it("has a descriptor and a default for every layer id", () => {
    expect(LAYER_REGISTRY.map((d) => d.id).sort()).toEqual([...LAYER_IDS].sort());
    const defaults = defaultLayerStates();
    expect(Object.keys(defaults).sort()).toEqual([...LAYER_IDS].sort());
    // A fresh copy each time, so the store never shares the constant.
    expect(defaultLayerStates().stations).not.toBe(defaults.stations);
  });

  it("does not build style layers as deck layers", () => {
    const layers = buildDeckLayers(LAYER_REGISTRY, defaultLayerStates(), ctx);
    expect(layers.some((layer) => layer.id === "basemap")).toBe(false);
  });
});

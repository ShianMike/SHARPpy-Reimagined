import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { panelLayers, useMapStore } from "./mapStore";
import { ORDERABLE_LAYER_IDS } from "../map/layers/registry";
import { applyPreferences, currentPreferences, DEFAULT_PREFERENCES, parsePreferences } from "./preferences";

const mapInitial = useMapStore.getInitialState();
let warn: MockInstance<typeof console.warn>;
beforeEach(() => {
  useMapStore.setState(mapInitial, true);
  warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => { vi.restoreAllMocks(); });

const VALID = {
  ...DEFAULT_PREFERENCES,
  graphicsOverride: "low",
  layers: { stations: { visible: false, opacity: 0.5 }, basemap: { visible: true, opacity: 0.8 } },
  productKey: null,
  panels: [],
  layerPanelCollapsed: true,
  lastSelectedStationId: "72357",
  camera: { center: [-97.4, 35.2], zoom: 6 },
};

describe("parsePreferences", () => {
  it("restores the legend category with the existing layer preferences", () => {
    useMapStore.getState().setLayerPanelGroup("legend");
    const saved = JSON.stringify(currentPreferences());
    useMapStore.setState(mapInitial, true);
    applyPreferences(parsePreferences(saved));
    expect(useMapStore.getState().layerPanelGroup).toBe("legend");
  });
  it("restores removed radar tilts to the default while preserving supported cuts", () => {
    for (const radarTilt of ["x", "y", "z", "0", "1"]) {
      const parsed = parsePreferences(JSON.stringify({ ...VALID, radarTilt, livePanels: [{ radarTilt }] }));
      const expected = radarTilt === "x" || radarTilt === "y" ? "0" : radarTilt;
      expect(parsed.radarTilt).toBe(expected);
      expect(parsed.livePanels[0]?.radarTilt).toBe(expected);
    }
  });

  it("restores both projections and uses the flat default for old or invalid values", () => {
    for (const projection of ["mercator", "globe"] as const) {
      useMapStore.getState().setProjection(projection);
      const saved = JSON.stringify(currentPreferences());
      useMapStore.setState(mapInitial, true);
      applyPreferences(parsePreferences(saved));
      expect(useMapStore.getState().projection).toBe(projection);
    }
    for (const projection of [undefined, "unknown", null, 1]) {
      expect(parsePreferences(JSON.stringify({ version: 1, projection })).projection).toBe("mercator");
    }
    expect(warn).toHaveBeenCalledTimes(3);
  });

  it("restores both sampling choices and defaults old or invalid preferences to smooth", () => {
    for (const smoothing of [true, false]) {
      useMapStore.getState().setFieldSmoothing(smoothing);
      const saved = JSON.stringify(currentPreferences());
      useMapStore.setState(mapInitial, true);
      applyPreferences(parsePreferences(saved));
      expect(useMapStore.getState().layers["model-field"].smoothing).toBe(smoothing);
    }
    for (const smoothing of [undefined, "true"]) {
      const parsed = parsePreferences(JSON.stringify({ version: 1, layers: { "model-field": { visible: true, opacity: 0.7, smoothing } } }));
      expect(parsed.layers["model-field"]).toEqual({ visible: true, opacity: 0.7, smoothing: true });
    }
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("loads a valid object without a warning", () => {
    expect(parsePreferences(JSON.stringify(VALID))).toEqual(VALID);
    expect(parsePreferences(null)).toEqual(DEFAULT_PREFERENCES);
    expect(warn).not.toHaveBeenCalled();
  });

  it("falls back to the defaults for corrupt JSON and for another version, with one warning each", () => {
    expect(parsePreferences("{not json")).toEqual(DEFAULT_PREFERENCES);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(parsePreferences(JSON.stringify({ ...VALID, version: 2 }))).toEqual(DEFAULT_PREFERENCES);
    expect(parsePreferences("[]")).toEqual(DEFAULT_PREFERENCES);
    expect(warn).toHaveBeenCalledTimes(3);
  });

  it("replaces each field of the wrong type by its default, with one warning per field", () => {
    const parsed = parsePreferences(
      JSON.stringify({
        ...VALID,
        graphicsOverride: "ultra",
        layerPanelCollapsed: "yes",
        lastSelectedStationId: 42,
        layers: { stations: { visible: "no", opacity: 1 }, basemap: { visible: true, opacity: 0.8 } },
      }),
    );
    expect(parsed).toEqual({
      ...VALID,
      graphicsOverride: "auto",
      layerPanelCollapsed: false,
      lastSelectedStationId: null,
      layers: { basemap: { visible: true, opacity: 0.8 } },
    });
    expect(warn).toHaveBeenCalledTimes(4);
    expect(String(warn.mock.calls[0]?.[0])).toContain("graphicsOverride");
  });

  it("ignores unknown layer ids silently and clamps opacity", () => {
    const parsed = parsePreferences(
      JSON.stringify({
        version: 1,
        // "wind-barbs" is no layer of this version ("radar" was, before Phase 6, the unknown id here).
        layers: { "wind-barbs": { visible: true, opacity: 1 }, stations: { visible: true, opacity: 2 }, basemap: { visible: true, opacity: -1 } },
      }),
    );
    expect(parsed.layers).toEqual({ stations: { visible: true, opacity: 1 }, basemap: { visible: true, opacity: 0 } });
    expect(warn).not.toHaveBeenCalled();
  });

  it("validates saved panels without dropping valid neighbours or layer settings", () => {
    const parsed = parsePreferences(JSON.stringify({
      version: 1,
      productKey: 42,
      panels: [
        null,
        { productKey: "refc", layers: { "station-labels": { visible: true, opacity: 2 }, radar: { visible: "yes", opacity: 1 }, unknown: { visible: true, opacity: 1 } } },
        { productKey: " ", layers: { "model-field": { visible: true, opacity: 0.7 } } },
        { productKey: "ignored" },
      ],
    }));
    expect(parsed.productKey).toBeNull();
    expect(parsed.panels).toEqual([
      { productKey: null, layers: {} },
      { productKey: "refc", layers: { "station-labels": { visible: true, opacity: 1 } } },
      { productKey: null, layers: { "model-field": { visible: true, opacity: 0.7, smoothing: true } } },
    ]);
    applyPreferences(parsed);
    expect(useMapStore.getState().panels).toHaveLength(3);
    expect(panelLayers(useMapStore.getState(), 1)["station-labels"].visible).toBe(false);
    expect(panelLayers(useMapStore.getState(), 2)["station-labels"].visible).toBe(true);
    expect(parsePreferences(JSON.stringify({ version: 1, panels: {} })).panels).toEqual([]);
  });

  it("drops an out-of-range or malformed camera with one warning each", () => {
    for (const camera of [{ center: [-97, 35], zoom: 20 }, { center: [-97, 95], zoom: 5 }, { center: [-97], zoom: 5 }, "x"]) {
      expect(parsePreferences(JSON.stringify({ version: 1, camera })).camera).toBeNull();
    }
    expect(warn).toHaveBeenCalledTimes(4);
    expect(parsePreferences(JSON.stringify({ version: 1, camera: null })).camera).toBeNull();
    expect(warn).toHaveBeenCalledTimes(4);
  });

  it("validates workspace options and repairs duplicate, missing or retired layer ids", () => {
    const parsed = parsePreferences(JSON.stringify({ version: 1, panelLayout: 3, activePanel: 2,
      modelKey: false, radarProduct: 2, radarScope: "other", radarSite: {}, satelliteChannel: "other",
      reportWindowS: -1, reportFilter: "other", layerPanelGroup: false, layerOrder: false }));
    expect(parsed).toEqual(DEFAULT_PREFERENCES);
    const order = parsePreferences(JSON.stringify({ version: 1, layerOrder: ["radar", "retired", "radar", "model-field"] })).layerOrder;
    expect(order).toEqual(["radar", "model-field", ...ORDERABLE_LAYER_IDS.filter((id) => id !== "radar" && id !== "model-field")]);
    expect(parsePreferences(JSON.stringify({ version: 1, panelLayout: 2, activePanel: 3 })).activePanel).toBe(0);
  });
});

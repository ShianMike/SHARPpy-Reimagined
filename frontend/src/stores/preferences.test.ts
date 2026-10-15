import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { useAppStore } from "./appStore";
import { useGraphicsStore } from "./graphicsStore";
import { panelLayers, panelProduct, useMapStore } from "./mapStore";
import {
  applyPreferences,
  createPreferencesRepository,
  currentPreferences,
  DEFAULT_PREFERENCES,
  parsePreferences,
  PREFERENCES_KEY,
  SAVE_DEBOUNCE_MS,
  startPreferenceSync,
  type PreferenceStorage,
} from "./preferences";

const appInitial = useAppStore.getState(), mapInitial = useMapStore.getState(), graphicsInitial = useGraphicsStore.getState();
let warn: MockInstance<typeof console.warn>;
beforeEach(() => {
  useAppStore.setState(appInitial, true);
  useMapStore.setState(mapInitial, true);
  useGraphicsStore.setState(graphicsInitial, true);
  warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => { vi.useRealTimers(); });

function memoryStorage(initial: string | null = null): PreferenceStorage & { setItem: ReturnType<typeof vi.fn> } {
  let value = initial;
  return {
    getItem: (key) => (key === PREFERENCES_KEY ? value : null),
    setItem: vi.fn((key: string, text: string) => {
      if (key === PREFERENCES_KEY) {
        value = text;
      }
    }),
  };
}

const VALID = {
  version: 1,
  graphicsOverride: "low",
  layers: { stations: { visible: false, opacity: 0.5 }, basemap: { visible: true, opacity: 0.8 } },
  productKey: null,
  panels: [],
  layerPanelCollapsed: true,
  lastSelectedStationId: "72357",
  camera: { center: [-97.4, 35.2], zoom: 6 },
};

describe("parsePreferences", () => {
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
    expect(panelLayers(useMapStore.getState(), 2)["station-labels"].visible).toBe(false);
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
});

describe("the repository", () => {
  it("falls back to memory with one warning when storage throws", () => {
    const broken: PreferenceStorage = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    const repository = createPreferencesRepository(() => broken);
    expect(repository.load()).toEqual(DEFAULT_PREFERENCES);
    repository.save({ ...DEFAULT_PREFERENCES, graphicsOverride: "high" });
    expect(repository.load().graphicsOverride).toBe("high");
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("switches to memory when only writes fail", () => {
    const storage = memoryStorage();
    storage.setItem.mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const repository = createPreferencesRepository(() => storage);
    repository.save({ ...DEFAULT_PREFERENCES, layerPanelCollapsed: true });
    repository.save({ ...DEFAULT_PREFERENCES, layerPanelCollapsed: true, graphicsOverride: "low" });
    expect(repository.load()).toMatchObject({ layerPanelCollapsed: true, graphicsOverride: "low" });
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledTimes(1);
  });
});

describe("applying and saving", () => {
  it("starts fresh panels without labels", () => {
    applyPreferences(DEFAULT_PREFERENCES);
    for (let panel = 0; panel < 4; panel++) expect(panelLayers(useMapStore.getState(), panel)["station-labels"].visible).toBe(false);
    expect(warn).not.toHaveBeenCalled();
  });

  it("starts saved panels without labels and keeps session choices across layout switches", () => {
    vi.useFakeTimers();
    const storage = memoryStorage();
    const repository = createPreferencesRepository(() => storage);
    const stop = startPreferenceSync(repository);
    useMapStore.getState().setPanelLayout(4);
    for (const [panel, product] of ["refc", "tmp-2m", "rh-2m", "hgt-wind-500"].entries()) {
      const store = useMapStore.getState();
      store.setActivePanel(panel);
      store.setProduct(product);
      store.setLayerVisible("station-labels", panel % 2 === 0);
      store.setLayerVisible("radar", panel % 2 === 1);
      store.setLayerOpacity("model-field", (panel + 1) / 5);
      store.setFieldSmoothing(panel % 2 === 0);
    }
    const expected = currentPreferences();
    useMapStore.getState().setPanelLayout(1);
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    stop();
    useMapStore.setState(mapInitial, true);
    applyPreferences(repository.load());
    for (let panel = 0; panel < 4; panel++) {
      const saved = panel === 0 ? expected : expected.panels[panel - 1];
      expect(panelLayers(useMapStore.getState(), panel)).toEqual({ ...saved?.layers,
        "station-labels": { ...saved?.layers["station-labels"], visible: false } });
      useMapStore.getState().setPanelLayout(4);
      useMapStore.getState().setActivePanel(panel);
      useMapStore.getState().setLayerVisible("station-labels", panel % 2 === 0);
    }
    for (const layout of [2, 4, 1, 4] as const) {
      useMapStore.getState().setPanelLayout(layout);
      expect(currentPreferences()).toEqual(expected);
      for (let panel = 0; panel < 4; panel++) {
        const saved = panel === 0 ? expected : expected.panels[panel - 1];
        expect(panelProduct(useMapStore.getState(), panel)).toBe(saved?.productKey);
        expect(panelLayers(useMapStore.getState(), panel)).toEqual(saved?.layers);
      }
    }
    expect(warn).not.toHaveBeenCalled();
  });

  it.each([0, 1, 3])("saves product-only and layer-only edits in panel %i", (panel) => {
    vi.useFakeTimers();
    const storage = memoryStorage();
    const repository = createPreferencesRepository(() => storage);
    const stop = startPreferenceSync(repository);
    const store = useMapStore.getState();
    store.setPanelLayout(4);
    store.setActivePanel(panel);
    store.setProduct("tmp-2m");
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    const savedProduct = repository.load();
    expect((panel === 0 ? savedProduct : savedProduct.panels[panel - 1])?.productKey).toBe("tmp-2m");
    store.setLayerVisible("station-labels", true);
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS);
    expect(storage.setItem).toHaveBeenCalledTimes(2);
    const savedLabels = repository.load();
    expect((panel === 0 ? savedLabels : savedLabels.panels[panel - 1])?.layers["station-labels"]?.visible).toBe(true);
    stop();
  });

  it("applies the stored values to the stores, keeping a restored station pending", () => {
    applyPreferences(parsePreferences(JSON.stringify(VALID)));
    expect(useGraphicsStore.getState().override).toBe("low");
    expect(useMapStore.getState().layers.stations).toEqual({ visible: false, opacity: 0.5 });
    expect(useMapStore.getState().layers["station-labels"]).toEqual(mapInitial.layers["station-labels"]);
    expect(useMapStore.getState().layerPanelCollapsed).toBe(true);
    expect(useMapStore.getState().cameraSnapshot).toEqual({ center: [-97.4, 35.2], zoom: 6 });
    expect(useAppStore.getState().selectedStationId).toBeNull();
    expect(useAppStore.getState().pendingStationId).toBe("72357");
    expect(currentPreferences().lastSelectedStationId).toBe("72357");
    expect(useAppStore.getState().reconcilePendingSelection((id) => id === "72357")).toBe("selected");
    expect(useAppStore.getState().selectedStationId).toBe("72357");
  });

  it("clears a restored id that is not in the catalogue, so the warning does not repeat", () => {
    applyPreferences(parsePreferences(JSON.stringify(VALID)));
    expect(useAppStore.getState().reconcilePendingSelection(() => false)).toBe("dropped");
    expect(currentPreferences().lastSelectedStationId).toBeNull();
  });

  it("debounces writes by 300 ms into one save of the current values", () => {
    vi.useFakeTimers();
    const storage = memoryStorage();
    const stop = startPreferenceSync(createPreferencesRepository(() => storage));
    useGraphicsStore.getState().setOverride("high");
    useMapStore.getState().setLayerOpacity("basemap", 0.3);
    useMapStore.getState().setFieldSmoothing(true);
    useMapStore.getState().setLayerPanelCollapsed(true);
    useAppStore.getState().selectStation("72357");
    useMapStore.getState().setCameraSnapshot({ center: [-90, 40], zoom: 4 });
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS - 1);
    expect(storage.setItem).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    const saved = parsePreferences(storage.getItem(PREFERENCES_KEY));
    expect(saved).toMatchObject({
      graphicsOverride: "high",
      layerPanelCollapsed: true,
      lastSelectedStationId: "72357",
      camera: { center: [-90, 40], zoom: 4 },
    });
    expect(saved.layers.basemap?.opacity).toBeCloseTo(0.3);
    expect(saved.layers["model-field"]?.smoothing).toBe(true);
    useAppStore.getState().clearSelection();
    stop();
    expect(storage.setItem).toHaveBeenCalledTimes(2);
    expect(parsePreferences(storage.getItem(PREFERENCES_KEY)).lastSelectedStationId).toBeNull();
    useMapStore.getState().setLayerPanelCollapsed(false);
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS);
    expect(storage.setItem).toHaveBeenCalledTimes(2);
  });
});

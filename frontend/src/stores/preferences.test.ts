import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { useAppStore } from "./appStore";
import { useGraphicsStore } from "./graphicsStore";
import { panelLayers, panelProduct, useMapStore } from "./mapStore";
import { useForecastStore } from "./forecastStore";
import { ORDERABLE_LAYER_IDS } from "../map/layers/registry";
import {
  applyPreferences,
  createPreferencesRepository,
  currentPreferences,
  DEFAULT_PREFERENCES,
  parsePreferences,
  PREFERENCES_KEY,
  resetLayerSettings,
  SAVE_DEBOUNCE_MS,
  startPreferenceSync,
  type PreferenceStorage,
} from "./preferences";

const appInitial = useAppStore.getState(), mapInitial = useMapStore.getState(), graphicsInitial = useGraphicsStore.getState();
const forecastInitial = useForecastStore.getState();
let warn: MockInstance<typeof console.warn>;
beforeEach(() => {
  useAppStore.setState(appInitial, true);
  useMapStore.setState(mapInitial, true);
  useGraphicsStore.setState(graphicsInitial, true);
  useForecastStore.setState(forecastInitial, true);
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
  ...DEFAULT_PREFERENCES,
  graphicsOverride: "low",
  layers: { stations: { visible: false, opacity: 0.5 }, basemap: { visible: true, opacity: 0.8 } },
  productKey: null,
  panels: [],
  layerPanelCollapsed: true,
  lastSelectedStationId: "72357",
  camera: { center: [-97.4, 35.2], zoom: 6 },
};

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
    expect(repository.save({ ...DEFAULT_PREFERENCES, graphicsOverride: "high" })).toBe(false);
    expect(repository.load().graphicsOverride).toBe("high");
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("switches to memory when only writes fail", () => {
    const storage = memoryStorage();
    storage.setItem.mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const repository = createPreferencesRepository(() => storage);
    expect(repository.save({ ...DEFAULT_PREFERENCES, layerPanelCollapsed: true })).toBe(false);
    expect(repository.save({ ...DEFAULT_PREFERENCES, layerPanelCollapsed: true, graphicsOverride: "low" })).toBe(false);
    expect(repository.load()).toMatchObject({ layerPanelCollapsed: true, graphicsOverride: "low" });
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("reports successful writes as persistent", () => {
    const storage = memoryStorage();
    const repository = createPreferencesRepository(() => storage);
    expect(repository.save({ ...DEFAULT_PREFERENCES, graphicsOverride: "high" })).toBe(true);
    expect(repository.load().graphicsOverride).toBe("high");
  });
});

describe("applying and saving", () => {
  it("starts fresh panels without labels", () => {
    applyPreferences(DEFAULT_PREFERENCES);
    for (let panel = 0; panel < 4; panel++) expect(panelLayers(useMapStore.getState(), panel)["station-labels"].visible).toBe(false);
    expect(warn).not.toHaveBeenCalled();
  });

  it("restores every panel's labels, products and layers and keeps them across layout switches", () => {
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
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS);
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    stop();
    useMapStore.setState(mapInitial, true);
    applyPreferences(repository.load());
    expect(currentPreferences()).toEqual(expected);
    expect(useMapStore.getState()).toMatchObject({ panelLayout: 4, activePanel: 3 });
    for (let panel = 0; panel < 4; panel++) {
      const saved = panel === 0 ? expected : expected.panels[panel - 1];
      expect(panelLayers(useMapStore.getState(), panel)).toEqual(saved?.layers);
    }
    for (const layout of [2, 4, 1, 4] as const) {
      useMapStore.getState().setPanelLayout(layout);
      for (let panel = 0; panel < 4; panel++) {
        const saved = panel === 0 ? expected : expected.panels[panel - 1];
        expect(panelProduct(useMapStore.getState(), panel)).toBe(saved?.productKey);
        expect(panelLayers(useMapStore.getState(), panel)).toEqual(saved?.layers);
      }
    }
    expect(warn).not.toHaveBeenCalled();
  });

  it("flushes the full weather setup on close and reopens the chosen model on a fresh run", () => {
    const storage = memoryStorage();
    const repository = createPreferencesRepository(() => storage);
    const stop = startPreferenceSync(repository);
    const map = useMapStore.getState();
    map.setPanelLayout(4);
    map.setActivePanel(2);
    map.setLayerVisible("radar", true);
    map.moveLayer("model-field", 1);
    map.setRadarScope("site");
    map.setRadarSite("KTLX");
    map.setRadarProduct("N0U");
    map.setSatelliteChannel("visible");
    map.setReportWindowS(43200);
    map.setReportFilter("tornado");
    map.setLayerPanelGroup("map");
    const forecast = useForecastStore.getState();
    forecast.setModel("namnest");
    forecast.setRun("2026-10-16T00:00:00Z");
    forecast.setHour(12);
    forecast.setPlaying(true);
    const expected = currentPreferences();
    stop();
    expect(storage.setItem).toHaveBeenCalledTimes(1);
    useMapStore.setState(mapInitial, true);
    useForecastStore.setState(forecastInitial, true);
    applyPreferences(repository.load());
    expect(currentPreferences()).toEqual(expected);
    expect(useForecastStore.getState()).toMatchObject({ modelKey: "namnest", runTime: null, forecastHour: null, playing: false });
    expect(warn).not.toHaveBeenCalled();
  });

  it("saves edits that only change weather options or the selected model", () => {
    vi.useFakeTimers();
    const storage = memoryStorage();
    const repository = createPreferencesRepository(() => storage);
    const stop = startPreferenceSync(repository);
    useMapStore.getState().setReportFilter("hail");
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS);
    expect(repository.load().reportFilter).toBe("hail");
    useForecastStore.getState().setModel("rrfs");
    vi.advanceTimersByTime(SAVE_DEBOUNCE_MS);
    expect(repository.load().modelKey).toBe("rrfs");
    expect(storage.setItem).toHaveBeenCalledTimes(2);
    stop();
  });

  it("resets layers and weather options in all panels while keeping products, layout and location", () => {
    const map = useMapStore.getState();
    map.setPanelLayout(4);
    for (let panel = 0; panel < 4; panel++) {
      map.setActivePanel(panel);
      map.setLayerVisible("station-labels", true);
      map.setLayerOpacity("radar", 0.1);
      map.setFieldSmoothing(false);
      map.setProduct(`product-${String(panel)}`);
    }
    map.setRadarScope("site");
    map.setRadarSite("KTLX");
    map.setRadarProduct("N0U");
    map.setSatelliteChannel("visible");
    map.setReportWindowS(7200);
    map.setReportFilter("tornado");
    map.setLayerPanelGroup("map");
    map.setCameraSnapshot({ center: [-90, 40], zoom: 5 });
    resetLayerSettings();
    expect(useMapStore.getState()).toMatchObject({ panelLayout: 4, activePanel: 3, radarScope: "mosaic", radarSite: null,
      radarProduct: null, satelliteChannel: "infrared", reportWindowS: 21600, reportFilter: "all", layerPanelGroup: "weather",
      cameraSnapshot: { center: [-90, 40], zoom: 5 }, layerOrder: [...ORDERABLE_LAYER_IDS] });
    for (let panel = 0; panel < 4; panel++) {
      expect(panelLayers(useMapStore.getState(), panel)).toEqual(panelLayers(mapInitial, panel));
      expect(panelProduct(useMapStore.getState(), panel)).toBe(`product-${String(panel)}`);
    }
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

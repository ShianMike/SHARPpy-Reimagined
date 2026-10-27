// The preferences repository (DESIGN §2g, DESIGN-frontend F3): one versioned
// localStorage key, sharppy.prefs.v1, validated field by field on load. A bad
// field falls back to its default with one warning; unknown layer ids are
// ignored; opacity is clamped; the camera must be finite and inside the zoom
// range. Writes are debounced 300 ms (the camera snapshot itself is written on
// moveend, debounced 500 ms, by MapRuntime). A restored station id waits in
// pendingStationId until the first successful catalogue load. If storage
// throws, the preferences live in memory for the session, with one warning.

import { useAppStore } from "./appStore";
import { useGraphicsStore } from "./graphicsStore";
import { useMapStore } from "./mapStore";
import { useForecastStore } from "./forecastStore";
import { useUiStore } from "./uiStore";
import { DEFAULT_PREFERENCES, parsePreferences, PREFERENCES_KEY, warnPreferences as warn, type Preferences } from "./preferenceValues";
export { DEFAULT_PREFERENCES, parsePreferences, PREFERENCES_KEY, type Preferences } from "./preferenceValues";

export const SAVE_DEBOUNCE_MS = 300;

/** The subset of Storage the repository uses. */
export type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

export interface PreferencesRepository {
  load: () => Preferences;
  /** True only when the preferences were written to persistent storage. */
  save: (preferences: Preferences) => boolean;
}

/** localStorage behind a memory fallback: the first throw switches to memory, with one warning. */
export function createPreferencesRepository(getStorage: () => PreferenceStorage): PreferencesRepository {
  let memory: string | null = null;
  let useMemory = false;
  const fallBack = (error: unknown): void => {
    if (!useMemory) {
      useMemory = true;
      warn(`storage is unavailable, so preferences last for this session only (${String(error)})`);
    }
  };
  const read = (): string | null => {
    if (!useMemory) {
      try {
        return getStorage().getItem(PREFERENCES_KEY);
      } catch (error) {
        fallBack(error);
      }
    }
    return memory;
  };
  return {
    load: () => parsePreferences(read()),
    save: (preferences) => {
      const text = JSON.stringify(preferences);
      memory = text;
      if (!useMemory) {
        try {
          getStorage().setItem(PREFERENCES_KEY, text);
        } catch (error) {
          fallBack(error);
        }
      }
      return !useMemory;
    },
  };
}

/** Writes loaded preferences into the stores' initial values (before the first render). */
export function applyPreferences(preferences: Preferences): void {
  useGraphicsStore.getState().setOverride(preferences.graphicsOverride);
  // Resume the chosen model with fresh data, rather than an expired saved run.
  useForecastStore.setState({ modelKey: preferences.modelKey, runTime: null, forecastHour: null, playing: false });
  const initial = useMapStore.getInitialState();
  useMapStore.setState({
    layers: { ...initial.layers, ...preferences.layers },
    livePanels: initial.livePanels.map((panel, index) => ({ ...panel, ...preferences.livePanels[index], layers: { ...panel.layers, ...preferences.livePanels[index]?.layers } })),
    productKey: preferences.productKey,
    panels: initial.panels.map((panel, index) => {
      const saved = preferences.panels[index];
      return { productKey: saved === undefined ? panel.productKey : saved.productKey,
        layers: { ...panel.layers, ...saved?.layers } };
    }),
    panelLayout: preferences.panelLayout,
    projection: preferences.projection,
    activePanel: preferences.activePanel,
    layerOrder: preferences.layerOrder,
    radarProduct: preferences.radarProduct,
    radarTilt: preferences.radarTilt,
    radarScope: preferences.radarScope,
    radarSite: preferences.radarSite,
    satelliteChannel: preferences.satelliteChannel,
    reportWindowS: preferences.reportWindowS,
    reportFilter: preferences.reportFilter,
    layerPanelGroup: preferences.layerPanelGroup,
    layerPanelCollapsed: preferences.layerPanelCollapsed,
    cameraSnapshot: preferences.camera,
  });
  useAppStore.getState().setPendingStation(preferences.lastSelectedStationId);
}

/** The preferences the stores hold now. */
export function currentPreferences(): Preferences {
  const app = useAppStore.getState();
  const map = useMapStore.getState();
  return {
    version: 1,
    graphicsOverride: useGraphicsStore.getState().override,
    modelKey: useForecastStore.getState().modelKey,
    panelLayout: map.panelLayout,
    projection: map.projection,
    activePanel: map.activePanel,
    layerOrder: [...map.layerOrder],
    radarProduct: map.radarProduct,
    radarTilt: map.radarTilt,
    radarScope: map.radarScope,
    radarSite: map.radarSite,
    satelliteChannel: map.satelliteChannel,
    reportWindowS: map.reportWindowS,
    reportFilter: map.reportFilter,
    layerPanelGroup: map.layerPanelGroup,
    layers: { ...map.layers },
    livePanels: map.livePanels.map((panel) => ({ ...panel, layers: { ...panel.layers } })),
    productKey: map.productKey,
    panels: map.panels.map(({ productKey, layers }) => ({ productKey, layers: { ...layers } })),
    layerPanelCollapsed: map.layerPanelCollapsed,
    // A restored id still waiting for its catalogue is kept, not lost.
    lastSelectedStationId: app.selectedStationId ?? app.pendingStationId,
    camera: map.cameraSnapshot,
  };
}

/** Reset every panel's layers and shared weather options, keeping its products and view. */
export function resetLayerSettings(): void {
  const initial = useMapStore.getInitialState();
  if (useMapStore.getState().view === "live") {
    useMapStore.setState({ livePanels: initial.livePanels, reportWindowS: initial.reportWindowS,
      reportFilter: initial.reportFilter, layerPanelGroup: initial.layerPanelGroup });
    return;
  }
  useMapStore.setState((state) => ({
    layers: initial.layers,
    panels: state.panels.map((panel, index) => ({ ...panel, layers: initial.panels[index]?.layers ?? initial.layers })),
    layerOrder: initial.layerOrder,
    projection: initial.projection,
    radarProduct: initial.radarProduct,
    radarTilt: initial.radarTilt,
    radarScope: initial.radarScope,
    radarSite: initial.radarSite,
    satelliteChannel: initial.satelliteChannel,
    reportWindowS: initial.reportWindowS,
    reportFilter: initial.reportFilter,
    availabilityTime: initial.availabilityTime,
    layerPanelGroup: initial.layerPanelGroup,
  }));
}

/** Reset preferences without discarding open work or moving the map. */
export function resetAllSettings(): void {
  const { cameraSnapshot, focusRequest } = useMapStore.getState();
  useMapStore.setState({ ...useMapStore.getInitialState(), cameraSnapshot, focusRequest }, true);
  useForecastStore.setState(useForecastStore.getInitialState(), true);
  useUiStore.setState({ ...useUiStore.getInitialState(), lastVersion: useUiStore.getState().lastVersion }, true);
  useGraphicsStore.getState().setOverride(DEFAULT_PREFERENCES.graphicsOverride);
}

/** Subscribes the debounced writer to the stores; returns the function that stops it. */
export function startPreferenceSync(repository: PreferencesRepository, debounceMs = SAVE_DEBOUNCE_MS): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const flush = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    repository.save(currentPreferences());
  };
  const schedule = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
    }
    timer = setTimeout(flush, debounceMs);
  };
  const unsubscribers = [
    useGraphicsStore.subscribe((state) => state.override, schedule),
    useMapStore.subscribe(schedule),
    useForecastStore.subscribe((state) => state.modelKey, schedule),
    useAppStore.subscribe((state) => state.selectedStationId ?? state.pendingStationId, schedule),
  ];
  return () => {
    if (timer !== null) {
      flush();
    }
    for (const unsubscribe of unsubscribers) {
      unsubscribe();
    }
  };
}

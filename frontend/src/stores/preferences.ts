// The preferences repository (DESIGN §2g, DESIGN-frontend F3): one versioned
// localStorage key, sharppy.prefs.v1, validated field by field on load. A bad
// field falls back to its default with one warning; unknown layer ids are
// ignored; opacity is clamped; the camera must be finite and inside the zoom
// range. Writes are debounced 300 ms (the camera snapshot itself is written on
// moveend, debounced 500 ms, by MapRuntime). A restored station id waits in
// pendingStationId until the first successful catalogue load. If storage
// throws, the preferences live in memory for the session, with one warning.

import { isValidCamera, type CameraSnapshot } from "../map/runtime/camera";
import { clampOpacity, LAYER_IDS, type LayerId, type LayerState } from "../map/layers/registry";
import type { GraphicsOverride } from "../types/graphics";
import { useAppStore } from "./appStore";
import { useGraphicsStore } from "./graphicsStore";
import { MAX_PANELS, useMapStore } from "./mapStore";

export const PREFERENCES_KEY = "sharppy.prefs.v1";
export const SAVE_DEBOUNCE_MS = 300;
const OVERRIDES: readonly GraphicsOverride[] = ["auto", "low", "balanced", "high"];
const MAX_STATION_ID_LENGTH = 16;

export interface Preferences {
  version: 1;
  graphicsOverride: GraphicsOverride;
  /** The stored state of each known layer; a missing id keeps its default. */
  layers: Partial<Record<LayerId, LayerState>>;
  productKey: string | null;
  /** Panels 1 to 3, including those currently closed. */
  panels: { productKey: string | null; layers: Preferences["layers"] }[];
  layerPanelCollapsed: boolean;
  lastSelectedStationId: string | null;
  camera: CameraSnapshot | null;
}

export const DEFAULT_PREFERENCES: Preferences = {
  version: 1,
  graphicsOverride: "auto",
  layers: {},
  productKey: null,
  panels: [],
  layerPanelCollapsed: false,
  lastSelectedStationId: null,
  camera: null,
};

/** The subset of Storage the repository uses. */
export type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

const warn = (message: string): void => {
  console.warn(`[preferences] ${message}`);
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseLayer(id: LayerId, value: unknown): LayerState | null {
  if (!isRecord(value) || typeof value.visible !== "boolean" || typeof value.opacity !== "number") {
    warn(`layers.${id} is not {visible, opacity}; the default is used`);
    return null;
  }
  if (!Number.isFinite(value.opacity)) {
    warn(`layers.${id}.opacity is not finite; the default is used`);
    return null;
  }
  return {
    visible: value.visible,
    opacity: clampOpacity(value.opacity),
    ...(id === "model-field" ? { smoothing: field(value, "smoothing", isBoolean, true) } : {}),
  };
}

function parseLayers(value: unknown): Partial<Record<LayerId, LayerState>> {
  if (value === undefined) {
    return {};
  }
  if (!isRecord(value)) {
    warn("layers is not an object; the defaults are used");
    return {};
  }
  const layers: Partial<Record<LayerId, LayerState>> = {};
  for (const id of LAYER_IDS) {
    if (id in value) {
      const parsed = parseLayer(id, value[id]);
      if (parsed !== null) {
        layers[id] = parsed;
      }
    }
  }
  return layers; // Unknown ids are ignored.
}

function parsePanels(value: unknown): Preferences["panels"] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) {
    warn("panels is not an array; the defaults are used");
    return [];
  }
  return value.slice(0, MAX_PANELS - 1).map((panel: unknown, index) => {
    if (!isRecord(panel)) {
      warn(`panels.${String(index)} is not an object; the defaults are used`);
      return { productKey: null, layers: {} };
    }
    return { productKey: field(panel, "productKey", isProductKey, null), layers: parseLayers(panel.layers) };
  });
}

function parseCamera(value: unknown): CameraSnapshot | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (isRecord(value) && Array.isArray(value.center) && value.center.length === 2) {
    const [lng, lat] = value.center as unknown[];
    if (typeof lng === "number" && typeof lat === "number" && typeof value.zoom === "number") {
      const camera: CameraSnapshot = { center: [lng, lat], zoom: value.zoom };
      if (isValidCamera(camera)) {
        return camera;
      }
    }
  }
  warn("camera is not a finite in-range {center, zoom}; the initial view is used");
  return null;
}

/** One field: the stored value when valid, else the default with one warning. */
function field<T>(obj: Record<string, unknown>, key: string, valid: (value: unknown) => value is T, fallback: T): T {
  const value = obj[key];
  if (value === undefined) {
    return fallback;
  }
  if (valid(value)) {
    return value;
  }
  warn(`${key} has an invalid value; the default is used`);
  return fallback;
}

const isOverride = (value: unknown): value is GraphicsOverride => OVERRIDES.includes(value as GraphicsOverride);
const isBoolean = (value: unknown): value is boolean => typeof value === "boolean";
const isProductKey = (value: unknown): value is string | null =>
  value === null || (typeof value === "string" && value.trim() !== "" && value.length <= 128);
const isStationId = (value: unknown): value is string | null =>
  value === null || (typeof value === "string" && value.trim() !== "" && value.length <= MAX_STATION_ID_LENGTH);

/** Parses the stored text, field by field. */
export function parsePreferences(raw: string | null): Preferences {
  if (raw === null) {
    return DEFAULT_PREFERENCES;
  }
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    warn(`${PREFERENCES_KEY} is not valid JSON; the defaults are used`);
    return DEFAULT_PREFERENCES;
  }
  if (!isRecord(data) || data.version !== 1) {
    warn(`${PREFERENCES_KEY} is not a version 1 preferences object; the defaults are used`);
    return DEFAULT_PREFERENCES;
  }
  return {
    version: 1,
    graphicsOverride: field(data, "graphicsOverride", isOverride, "auto"),
    layers: parseLayers(data.layers),
    productKey: field(data, "productKey", isProductKey, null),
    panels: parsePanels(data.panels),
    layerPanelCollapsed: field(data, "layerPanelCollapsed", isBoolean, false),
    lastSelectedStationId: field(data, "lastSelectedStationId", isStationId, null),
    camera: parseCamera(data.camera),
  };
}

export interface PreferencesRepository {
  load: () => Preferences;
  save: (preferences: Preferences) => void;
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
    },
  };
}

/** Writes loaded preferences into the stores' initial values (before the first render). */
export function applyPreferences(preferences: Preferences): void {
  useGraphicsStore.getState().setOverride(preferences.graphicsOverride);
  useMapStore.setState((state) => ({
    // Start uncluttered on launch; layout switches keep the session's label choices.
    layers: { ...state.layers, ...preferences.layers, "station-labels": { ...state.layers["station-labels"], ...preferences.layers["station-labels"], visible: false } },
    productKey: preferences.productKey,
    panels: state.panels.map((panel, index) => {
      const saved = preferences.panels[index];
      const layers = { ...panel.layers, ...saved?.layers };
      return { productKey: saved === undefined ? panel.productKey : saved.productKey,
        layers: { ...layers, "station-labels": { ...layers["station-labels"], visible: false } } };
    }),
    layerPanelCollapsed: preferences.layerPanelCollapsed,
    cameraSnapshot: preferences.camera,
  }));
  useAppStore.getState().setPendingStation(preferences.lastSelectedStationId);
}

/** The preferences the stores hold now. */
export function currentPreferences(): Preferences {
  const app = useAppStore.getState();
  const map = useMapStore.getState();
  return {
    version: 1,
    graphicsOverride: useGraphicsStore.getState().override,
    layers: { ...map.layers },
    productKey: map.productKey,
    panels: map.panels.map(({ productKey, layers }) => ({ productKey, layers: { ...layers } })),
    layerPanelCollapsed: map.layerPanelCollapsed,
    // A restored id still waiting for its catalogue is kept, not lost.
    lastSelectedStationId: app.selectedStationId ?? app.pendingStationId,
    camera: map.cameraSnapshot,
  };
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
    useMapStore.subscribe((state) => state.layers, schedule),
    useMapStore.subscribe((state) => state.productKey, schedule),
    useMapStore.subscribe((state) => state.panels, schedule),
    useMapStore.subscribe((state) => state.layerPanelCollapsed, schedule),
    useMapStore.subscribe((state) => state.cameraSnapshot, schedule),
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

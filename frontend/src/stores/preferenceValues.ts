// Saved workspace values and their field-by-field validation.
import { isValidCamera, type CameraSnapshot } from "../map/runtime/camera";
import { clampOpacity, LAYER_IDS, ORDERABLE_LAYER_IDS, type LayerId, type LayerState, type OrderableLayerId } from "../map/layers/registry";
import type { GraphicsOverride } from "../types/graphics";
import { RADAR_TILTS, type RadarTilt } from "../types/map";
import { MAX_PANELS, PANEL_LAYOUTS, useMapStore, type MapState, type WeatherOptions } from "./mapStore";

export const PREFERENCES_KEY = "sharppy.prefs.v1";
const OVERRIDES: readonly GraphicsOverride[] = ["auto", "low", "balanced", "high"];
const MAX_STATION_ID_LENGTH = 16;
const initialMap = useMapStore.getInitialState();

export interface Preferences extends Pick<MapState, "panelLayout" | "projection" | "activePanel" | "layerOrder" | "radarProduct" | "radarScope" | "radarSite" | "radarTilt" | "satelliteChannel" | "reportWindowS" | "reportFilter" | "layerPanelGroup"> {
  version: 1;
  graphicsOverride: GraphicsOverride;
  modelKey: string | null;
  /** The stored state of each known layer; a missing id keeps its default. */
  layers: Partial<Record<LayerId, LayerState>>;
  livePanels: (WeatherOptions & { layers: Preferences["layers"] })[];
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
  modelKey: null,
  panelLayout: initialMap.panelLayout,
  projection: initialMap.projection,
  activePanel: initialMap.activePanel,
  layerOrder: [...initialMap.layerOrder],
  radarProduct: initialMap.radarProduct,
  radarTilt: initialMap.radarTilt,
  radarScope: initialMap.radarScope,
  radarSite: initialMap.radarSite,
  satelliteChannel: initialMap.satelliteChannel,
  reportWindowS: initialMap.reportWindowS,
  reportFilter: initialMap.reportFilter,
  layerPanelGroup: initialMap.layerPanelGroup,
  layers: {},
  livePanels: [],
  productKey: null,
  panels: [],
  layerPanelCollapsed: false,
  lastSelectedStationId: null,
  camera: null,
};

export const warnPreferences = (message: string): void => {
  console.warn(`[preferences] ${message}`);
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseLayer(id: LayerId, value: unknown): LayerState | null {
  if (!isRecord(value) || typeof value.visible !== "boolean" || typeof value.opacity !== "number") {
    warnPreferences(`layers.${id} is not {visible, opacity}; the default is used`);
    return null;
  }
  if (!Number.isFinite(value.opacity)) {
    warnPreferences(`layers.${id}.opacity is not finite; the default is used`);
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
    warnPreferences("layers is not an object; the defaults are used");
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
    warnPreferences("panels is not an array; the defaults are used");
    return [];
  }
  return value.slice(0, MAX_PANELS - 1).map((panel: unknown, index) => {
    if (!isRecord(panel)) {
      warnPreferences(`panels.${String(index)} is not an object; the defaults are used`);
      return { productKey: null, layers: {} };
    }
    return { productKey: field(panel, "productKey", isProductKey, null), layers: parseLayers(panel.layers) };
  });
}

function parseLayerOrder(value: unknown): OrderableLayerId[] {
  if (value !== undefined && !Array.isArray(value)) warnPreferences("layerOrder is not an array; the default is used");
  const saved = Array.isArray(value) ? value.filter((id): id is OrderableLayerId => ORDERABLE_LAYER_IDS.includes(id as OrderableLayerId)) : [];
  return [...new Set([...saved, ...ORDERABLE_LAYER_IDS])];
}

function parseLivePanels(value: unknown): Preferences["livePanels"] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_PANELS).map((item: unknown) => {
    const panel = isRecord(item) ? item : {};
    return {
      layers: parseLayers(panel.layers),
      radarProduct: field(panel, "radarProduct", isProductKey, null),
      radarTilt: field(panel, "radarTilt", isRadarTilt, "0"),
      radarScope: field(panel, "radarScope", (scope): scope is "mosaic" | "site" => scope === "mosaic" || scope === "site", "mosaic"),
      radarSite: field(panel, "radarSite", isStationId, null),
      satelliteChannel: field(panel, "satelliteChannel", (channel): channel is "infrared" | "visible" => channel === "infrared" || channel === "visible", "infrared"),
    };
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
  warnPreferences("camera is not a finite in-range {center, zoom}; the initial view is used");
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
  warnPreferences(`${key} has an invalid value; the default is used`);
  return fallback;
}

const isOverride = (value: unknown): value is GraphicsOverride => OVERRIDES.includes(value as GraphicsOverride);
const isBoolean = (value: unknown): value is boolean => typeof value === "boolean";
const isRadarTilt = (value: unknown): value is RadarTilt => RADAR_TILTS.some((tilt) => tilt.value === value);
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
    warnPreferences(`${PREFERENCES_KEY} is not valid JSON; the defaults are used`);
    return DEFAULT_PREFERENCES;
  }
  if (!isRecord(data) || data.version !== 1) {
    warnPreferences(`${PREFERENCES_KEY} is not a version 1 preferences object; the defaults are used`);
    return DEFAULT_PREFERENCES;
  }
  const panelLayout = field(data, "panelLayout", (value): value is MapState["panelLayout"] => PANEL_LAYOUTS.includes(value as MapState["panelLayout"]), DEFAULT_PREFERENCES.panelLayout);
  return {
    version: 1,
    graphicsOverride: field(data, "graphicsOverride", isOverride, "auto"),
    modelKey: field(data, "modelKey", isProductKey, null),
    panelLayout,
    projection: field(data, "projection", (value): value is MapState["projection"] => value === "mercator" || value === "globe", DEFAULT_PREFERENCES.projection),
    activePanel: field(data, "activePanel", (value): value is number => typeof value === "number" && Number.isInteger(value) && value >= 0 && value < panelLayout, 0),
    layerOrder: parseLayerOrder(data.layerOrder),
    radarProduct: field(data, "radarProduct", isProductKey, null),
    radarTilt: field(data, "radarTilt", isRadarTilt, "0"),
    radarScope: field(data, "radarScope", (value): value is MapState["radarScope"] => value === "mosaic" || value === "site", "mosaic"),
    radarSite: field(data, "radarSite", isStationId, null),
    satelliteChannel: field(data, "satelliteChannel", (value): value is MapState["satelliteChannel"] => value === "infrared" || value === "visible", "infrared"),
    reportWindowS: field(data, "reportWindowS", (value): value is number => [7200, 21600, 43200, 86400].includes(value as number), DEFAULT_PREFERENCES.reportWindowS),
    reportFilter: field(data, "reportFilter", (value): value is string => ["all", "tornado", "hail", "wind", "significant"].includes(value as string), "all"),
    layerPanelGroup: field(data, "layerPanelGroup", (value): value is MapState["layerPanelGroup"] => value === "weather" || value === "legend" || value === "map", "weather"),
    layers: parseLayers(data.layers),
    livePanels: parseLivePanels(data.livePanels),
    productKey: field(data, "productKey", isProductKey, null),
    panels: parsePanels(data.panels),
    layerPanelCollapsed: field(data, "layerPanelCollapsed", isBoolean, false),
    lastSelectedStationId: field(data, "lastSelectedStationId", isStationId, null),
    camera: parseCamera(data.camera),
  };
}

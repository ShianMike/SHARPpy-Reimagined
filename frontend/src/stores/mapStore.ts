// Map state (DESIGN-frontend F3, REQUEST §14 and §18): each panel's layer
// visibility, opacity, and field product, the weather layers' stacking order,
// the layout (1, 2, or 4 synchronized panels) and the panel the layer panel
// edits, the radar product, the station-availability time, the layer panel's
// collapsed state, the persisted camera snapshot, and focus requests. Panel 0
// is the single map's state (`layers`, `productKey`); panels 1 to 3 keep their
// own. Time, model, and run are shared and live in forecastStore.

import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type { CameraSnapshot } from "../map/runtime/camera";
import {
  clampOpacity,
  defaultLayerStates,
  moveInOrder,
  ORDERABLE_LAYER_IDS,
  type LayerId,
  type LayerState,
  type OrderableLayerId,
} from "../map/layers/registry";

/** A request to fly to a station; seq changes even when the station repeats. */
export interface FocusRequest {
  stationId: string;
  seq: number;
}

export const PANEL_LAYOUTS = [1, 2, 4] as const;
export type PanelLayout = (typeof PANEL_LAYOUTS)[number];
export const MAX_PANELS = 4;

/** Panels 1 to 3: their own field product and layers. */
export interface PanelState {
  /** Null until chosen: the panel then takes a default product. */
  productKey: string | null;
  layers: Record<LayerId, LayerState>;
}

export interface MapState {
  /** Panel 0's layers. */
  layers: Record<LayerId, LayerState>;
  /** Panel 0's field product; null takes the catalogue's default. */
  productKey: string | null;
  panels: PanelState[];
  panelLayout: PanelLayout;
  projection: "mercator" | "globe";
  /** The panel the layer panel edits, 0 to 3. */
  activePanel: number;
  /** Bottom first. */
  layerOrder: OrderableLayerId[];
  /** Null takes the backend's default radar product. */
  radarProduct: string | null;
  /** Which radar the layer draws: the national mosaic, or one antenna's own imagery. */
  radarScope: "mosaic" | "site";
  /** The pinned radar antenna; null follows the map centre. */
  radarSite: string | null;
  /** The imagery channel the GOES layer renders from. */
  satelliteChannel: "infrared" | "visible";
  /** The total query span of the storm reports window, in seconds. */
  reportWindowS: number;
  /** The provider's hazard-filter key for the storm reports. */
  reportFilter: string;
  /** Explicit UWyo launch, or null to follow the observed sounding form. */
  availabilityTime: string | null;
  layerPanelCollapsed: boolean;
  layerPanelGroup: "weather" | "map";
  /** For persistence only; written on moveend. */
  cameraSnapshot: CameraSnapshot | null;
  focusRequest: FocusRequest | null;
  /** Visibility of a layer in the active panel. */
  setLayerVisible: (id: LayerId, visible: boolean) => void;
  /** Opacity of a layer in the active panel, clamped to [0, 1]. */
  setLayerOpacity: (id: LayerId, opacity: number) => void;
  setFieldSmoothing: (smoothing: boolean) => void;
  /** The field product of the active panel. */
  setProduct: (key: string) => void;
  setPanelLayout: (layout: PanelLayout) => void;
  setProjection: (projection: MapState["projection"]) => void;
  setActivePanel: (panel: number) => void;
  moveLayer: (id: OrderableLayerId, direction: 1 | -1) => void;
  setRadarProduct: (key: string | null) => void;
  setRadarScope: (scope: "mosaic" | "site") => void;
  setRadarSite: (site: string | null) => void;
  setSatelliteChannel: (channel: "infrared" | "visible") => void;
  setReportWindowS: (windowS: number) => void;
  setReportFilter: (filter: string) => void;
  setAvailabilityTime: (time: string | null) => void;
  setLayerPanelCollapsed: (collapsed: boolean) => void;
  setLayerPanelGroup: (group: "weather" | "map") => void;
  setCameraSnapshot: (snapshot: CameraSnapshot) => void;
  requestFocus: (stationId: string) => void;
}

function extraPanel(): PanelState {
  const layers = defaultLayerStates();
  // A panel opened to compare fields shows its field from the start.
  layers["model-field"] = { ...layers["model-field"], visible: true };
  return { productKey: null, layers };
}

/** The layers a panel shows. */
export function panelLayers(state: Pick<MapState, "layers" | "panels">, panel: number): Record<LayerId, LayerState> {
  return panel === 0 ? state.layers : (state.panels[panel - 1]?.layers ?? state.layers);
}

/** The field product a panel chose, or null for its default. */
export function panelProduct(state: Pick<MapState, "productKey" | "panels">, panel: number): string | null {
  return panel === 0 ? state.productKey : (state.panels[panel - 1]?.productKey ?? null);
}

type LayerPatch = (layer: LayerState) => LayerState;

/** The state change that patches one layer of the active panel. */
function patchLayer(state: MapState, id: LayerId, patch: LayerPatch): Partial<MapState> {
  if (state.activePanel === 0) {
    return { layers: { ...state.layers, [id]: patch(state.layers[id]) } };
  }
  const index = state.activePanel - 1;
  const panels = state.panels.map((panel, i) =>
    i === index ? { ...panel, layers: { ...panel.layers, [id]: patch(panel.layers[id]) } } : panel,
  );
  return { panels };
}

export const useMapStore = create<MapState>()(
  subscribeWithSelector((set) => ({
    layers: defaultLayerStates(),
    productKey: null,
    panels: [extraPanel(), extraPanel(), extraPanel()],
    panelLayout: 1,
    projection: "mercator",
    activePanel: 0,
    layerOrder: [...ORDERABLE_LAYER_IDS],
    radarProduct: null,
    radarScope: "mosaic",
    radarSite: null,
    satelliteChannel: "infrared",
    reportWindowS: 21600,
    reportFilter: "all",
    availabilityTime: null,
    layerPanelCollapsed: false,
    layerPanelGroup: "weather",
    cameraSnapshot: null,
    focusRequest: null,
    setLayerVisible: (id, visible) => {
      set((state) => patchLayer(state, id, (layer) => ({ ...layer, visible })));
    },
    setLayerOpacity: (id, opacity) => {
      set((state) => patchLayer(state, id, (layer) => ({ ...layer, opacity: clampOpacity(opacity) })));
    },
    setFieldSmoothing: (smoothing) => {
      set((state) => patchLayer(state, "model-field", (layer) => ({ ...layer, smoothing })));
    },
    setProduct: (key) => {
      set((state) =>
        state.activePanel === 0
          ? { productKey: key }
          : { panels: state.panels.map((panel, i) => (i === state.activePanel - 1 ? { ...panel, productKey: key } : panel)) },
      );
    },
    setPanelLayout: (layout) => {
      set((state) => ({ panelLayout: layout, activePanel: state.activePanel < layout ? state.activePanel : 0 }));
    },
    setProjection: (projection) => { set({ projection }); },
    setActivePanel: (panel) => {
      set((state) => (panel >= 0 && panel < state.panelLayout ? { activePanel: panel } : {}));
    },
    moveLayer: (id, direction) => {
      set((state) => {
        const layers = panelLayers(state, state.activePanel);
        const moved = moveInOrder(state.layerOrder.filter((layer) => layers[layer].visible), id, direction);
        let index = 0;
        return { layerOrder: state.layerOrder.map((layer) => layers[layer].visible ? moved[index++] ?? layer : layer) };
      });
    },
    setRadarProduct: (key) => {
      set({ radarProduct: key });
    },
    setRadarScope: (scope) => {
      // The two scopes publish different products; the mosaic's key is not a
      // site's key, so switching clears the choice and each scope takes its own default.
      set({ radarScope: scope, radarProduct: null });
    },
    setRadarSite: (site) => {
      set({ radarSite: site });
    },
    setSatelliteChannel: (channel) => {
      set({ satelliteChannel: channel });
    },
    setReportWindowS: (windowS) => {
      set({ reportWindowS: windowS });
    },
    setReportFilter: (filter) => {
      set({ reportFilter: filter });
    },
    setAvailabilityTime: (time) => {
      set({ availabilityTime: time });
    },
    setLayerPanelCollapsed: (collapsed) => {
      set({ layerPanelCollapsed: collapsed });
    },
    setLayerPanelGroup: (layerPanelGroup) => {
      set({ layerPanelGroup });
    },
    setCameraSnapshot: (snapshot) => {
      set({ cameraSnapshot: { center: [snapshot.center[0], snapshot.center[1]], zoom: snapshot.zoom } });
    },
    requestFocus: (stationId) => {
      set((state) => ({ focusRequest: { stationId, seq: (state.focusRequest?.seq ?? 0) + 1 } }));
    },
  })),
);

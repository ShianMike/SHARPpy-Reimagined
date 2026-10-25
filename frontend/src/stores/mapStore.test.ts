import { beforeEach, describe, expect, it } from "vitest";
import { LAYER_DEFAULTS } from "../map/layers/registry";
import { mapProjection, panelLayers, panelProduct, useMapStore } from "./mapStore";

const initial = useMapStore.getState();

beforeEach(() => {
  useMapStore.setState(initial, true);
});

describe("mapStore", () => {
  it("keeps Live Tracking flat and preserves the Model Viewer projection", () => {
    useMapStore.getState().setProjection("globe");
    expect(mapProjection(useMapStore.getState())).toBe("globe");
    useMapStore.setState({ view: "live" });
    expect(mapProjection(useMapStore.getState())).toBe("mercator");
    useMapStore.getState().setProjection("mercator");
    expect(useMapStore.getState().projection).toBe("globe");
    useMapStore.getState().setProjection("globe");
    expect(mapProjection(useMapStore.getState())).toBe("mercator");
    useMapStore.setState({ view: "map" });
    expect(mapProjection(useMapStore.getState())).toBe("globe");
  });

  it("starts from the registry defaults", () => {
    const { layers, layerPanelCollapsed, cameraSnapshot, focusRequest } = useMapStore.getState();
    expect(layers).toEqual(LAYER_DEFAULTS);
    expect(layerPanelCollapsed).toBe(false);
    expect(cameraSnapshot).toBeNull();
    expect(focusRequest).toBeNull();
  });

  it("toggles one layer's visibility without touching the others", () => {
    useMapStore.getState().setLayerVisible("stations", false);
    const { layers } = useMapStore.getState();
    expect(layers.stations).toEqual({ visible: false, opacity: 1 });
    expect(layers.basemap).toEqual(LAYER_DEFAULTS.basemap);
  });

  it("clamps opacity to [0, 1]", () => {
    const { setLayerOpacity } = useMapStore.getState();
    setLayerOpacity("basemap", 0.35);
    expect(useMapStore.getState().layers.basemap.opacity).toBe(0.35);
    setLayerOpacity("basemap", 1.5);
    expect(useMapStore.getState().layers.basemap.opacity).toBe(1);
    setLayerOpacity("basemap", -0.2);
    expect(useMapStore.getState().layers.basemap.opacity).toBe(0);
    setLayerOpacity("basemap", Number.NaN);
    expect(useMapStore.getState().layers.basemap.opacity).toBe(0);
  });

  it("sequences focus requests, even for the same station", () => {
    const { requestFocus } = useMapStore.getState();
    requestFocus("72357");
    expect(useMapStore.getState().focusRequest).toEqual({ stationId: "72357", seq: 1 });
    requestFocus("72357");
    expect(useMapStore.getState().focusRequest).toEqual({ stationId: "72357", seq: 2 });
    requestFocus("73111");
    expect(useMapStore.getState().focusRequest).toEqual({ stationId: "73111", seq: 3 });
  });

  it("edits the active panel's layers and product, and keeps the others", () => {
    const store = useMapStore.getState();
    store.setPanelLayout(4);
    store.setActivePanel(2);
    useMapStore.getState().setLayerOpacity("radar", 0.25);
    useMapStore.getState().setFieldSmoothing(false);
    useMapStore.getState().setProduct("tmp-2m");
    let state = useMapStore.getState();
    expect(panelLayers(state, 2).radar.opacity).toBe(0.25);
    expect(panelLayers(state, 0).radar.opacity).toBe(LAYER_DEFAULTS.radar.opacity);
    expect(panelProduct(state, 2)).toBe("tmp-2m");
    expect(panelProduct(state, 0)).toBeNull();
    expect(panelLayers(state, 2)["model-field"].smoothing).toBe(false);
    for (const panel of [0, 1, 3]) expect(panelLayers(state, panel)["model-field"].smoothing).toBe(true);
    // An extra panel opens with its field on; panel 0 keeps its own default.
    expect(panelLayers(state, 1)["model-field"].visible).toBe(true);
    expect(panelLayers(state, 0)["model-field"].visible).toBe(false);
    // Back to one map: the active panel returns to 0, which panel 2's edits never touched.
    state.setPanelLayout(1);
    state = useMapStore.getState();
    expect(state.activePanel).toBe(0);
    state.setActivePanel(3);
    expect(useMapStore.getState().activePanel).toBe(0);
  });

  it("moves enabled weather layers past hidden and other-workspace layers, respecting stack boundaries", () => {
    useMapStore.getState().setLayerVisible("model-field", true);
    useMapStore.getState().setLayerVisible("radar", true);
    useMapStore.getState().setLayerVisible("spc-cat", true);
    useMapStore.getState().moveLayer("model-field", 1);
    expect(useMapStore.getState().layerOrder.slice(0, 5)).toEqual(["spc-cat", "satellite", "radar", "storm-reports", "model-field"]);
    const top = useMapStore.getState().layerOrder;
    useMapStore.getState().moveLayer("model-field", 1);
    expect(useMapStore.getState().layerOrder).toEqual(top);
    useMapStore.getState().moveLayer("satellite", -1);
    expect(useMapStore.getState().layerOrder).toEqual(top);
    useMapStore.getState().moveLayer("model-field", -1);
    expect(useMapStore.getState().layerOrder.slice(0, 5)).toEqual(["model-field", "satellite", "radar", "storm-reports", "spc-cat"]);
  });

  it("moves only the active live panel's visible overlays", () => {
    useMapStore.setState({ view: "live" });
    const store = useMapStore.getState();
    store.setPanelLayout(2); store.setActivePanel(1);
    store.setLayerVisible("model-field", true);
    store.setLayerVisible("satellite", true);
    const firstPanel = panelLayers(useMapStore.getState(), 0);
    store.moveLayer("radar", -1);
    expect(useMapStore.getState().layerOrder.slice(0, 3)).toEqual(["model-field", "radar", "satellite"]);
    const bottom = useMapStore.getState().layerOrder;
    store.moveLayer("radar", -1);
    expect(useMapStore.getState().layerOrder).toEqual(bottom);
    expect(panelLayers(useMapStore.getState(), 0)).toBe(firstPanel);
  });

  it("stores the camera snapshot and the panel state", () => {
    const center: [number, number] = [-97.4, 35.2];
    useMapStore.getState().setCameraSnapshot({ center, zoom: 5 });
    center[0] = 0;
    expect(useMapStore.getState().cameraSnapshot).toEqual({ center: [-97.4, 35.2], zoom: 5 });
    useMapStore.getState().setLayerPanelCollapsed(true);
    expect(useMapStore.getState().layerPanelCollapsed).toBe(true);
  });
});

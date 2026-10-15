import { expect, it, vi } from "vitest";
import { useMapStore } from "../../stores/mapStore";
import { defaultLayerStates } from "../layers/registry";
import { subscribeToStores } from "./runtimeWiring";

it("keeps label geometry on opacity and weather changes, refreshing it for label visibility", () => {
  const saved = useMapStore.getState();
  useMapStore.setState({ layers: defaultLayerStates(), panelLayout: 1 });
  const layersChanged = vi.fn();
  const unsubscribers = subscribeToStores(0, {
    selectionChanged: vi.fn(), layersChanged, focus: vi.fn(), profileChanged: vi.fn(), pixelRatioChanged: vi.fn(),
  });
  try {
    useMapStore.getState().setLayerOpacity("radar", 0.4);
    useMapStore.getState().setLayerVisible("radar", true);
    useMapStore.getState().setFieldSmoothing(false);
    useMapStore.setState({ layerOrder: [...useMapStore.getState().layerOrder].reverse() });
    expect(layersChanged.mock.calls).toEqual([[false], [false], [false], [false]]);
    useMapStore.getState().setLayerVisible("station-labels", true);
    expect(layersChanged).toHaveBeenLastCalledWith(true);
  } finally {
    unsubscribers.forEach((unsubscribe) => { unsubscribe(); });
    useMapStore.setState(saved);
  }
});

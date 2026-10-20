// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { useMapStore } from "../../stores/mapStore";
import { defaultLayerStates } from "../layers/registry";
import { createRuntimeWatch, subscribeToStores } from "./runtimeWiring";

it.each([1, 2, 3])("reports secondary panel %s failures and clears them after a successful render", (panel) => {
  const saved = useGraphicsStore.getState();
  const watch = createRuntimeWatch(panel);
  try {
    watch.overlayError("shader failed");
    expect(useGraphicsStore.getState().panelErrors[panel]?.message).toBe("shader failed");
    expect(useGraphicsStore.getState().renderer).toBe(saved.renderer);
    watch.afterRender();
    expect(useGraphicsStore.getState().panelErrors[panel]).toBeUndefined();
    watch.overlayError("later failure");
    expect(useGraphicsStore.getState().panelErrors[panel]?.message).toBe("later failure");
  } finally { watch.dispose(); useGraphicsStore.setState(saved, true); }
});

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

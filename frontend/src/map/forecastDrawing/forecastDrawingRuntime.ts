import type { Map as MapLibreMap, MapMouseEvent } from "maplibre-gl";
import { useAppStore } from "../../stores/appStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { wireBoxSelection } from "../box/boxRuntime";
import { wireForecastAreaDrag } from "./forecastAreaDrag";
import { forecastMapPoint } from "./forecastDrawingCoordinates";
import { forecastEditTargetAt } from "./forecastDrawingSelection";
import { forecastDrawingVertices } from "./forecastDrawingVertices";
import styles from "./forecastDrawingRuntime.module.css";

/** Owns map input and edit handles. The scene renders the committed and in-progress shapes. */
export function wireMapDrawingTools(map: MapLibreMap, panel: number, redraw: () => void): () => void {
  const releaseBox = wireBoxSelection(map, panel);
  const vertices = forecastDrawingVertices(map);
  const canvas = map.getCanvasContainer();
  let zoomWasEnabled = false;
  let drawing = false;
  const active = (): boolean => useForecastDrawingStore.getState().enabled
    && useMapStore.getState().view === "map" && useAppStore.getState().activeWorkspace === "map";
  const dragging = wireForecastAreaDrag(map, panel, active);
  const setDrawing = (value: boolean): void => {
    if (drawing === value) return;
    drawing = value;
    canvas.classList.toggle(styles.drawing ?? "", value);
    if (value) {
      zoomWasEnabled = map.doubleClickZoom.isEnabled();
      map.doubleClickZoom.disable();
    } else if (zoomWasEnabled) map.doubleClickZoom.enable();
  };
  const update = (): void => {
    dragging.sync();
    const state = useForecastDrawingStore.getState();
    const enabled = active();
    const wasDrawing = drawing;
    setDrawing(enabled && state.mode === "draw");
    if (drawing && !wasDrawing && panel === useMapStore.getState().activePanel) map.getCanvas().focus({ preventScroll: true });
    canvas.classList.toggle(styles.moveMode ?? "", enabled && state.editTool === "move" && !state.saving);
    const showVertices = enabled && state.mode === "edit" && state.editTool === "reshape" && panel === useMapStore.getState().activePanel;
    vertices(showVertices ? state.points : [], state.saving, showVertices && state.editing?.sourceId !== undefined);
  };
  const click = (event: MapMouseEvent): void => {
    const state = useForecastDrawingStore.getState();
    const input = event.originalEvent;
    if (!active() || state.saving || state.editTool === "move" || input.button !== 0
      || input.altKey || input.ctrlKey || input.metaKey || input.shiftKey
      || (input.target instanceof Element && input.target.closest("[data-forecast-handle]") !== null)) return;
    const point = forecastMapPoint(map, event.point, event.lngLat);
    if (point === null) return;
    if (state.mode === "draw") {
      state.addVertex(point);
    } else {
      const { product, polygons, results } = state;
      const target = forecastEditTargetAt(point, product, polygons[product], results[product]);
      if (target === undefined || !state.startEdit(target)) return;
      map.getCanvas().focus({ preventScroll: true });
    }
    useMapStore.getState().setActivePanel(panel);
  };
  map.on("click", click);
  const subscriptions = [
    useForecastDrawingStore.subscribe((state, previous) => {
      update();
      if (state.enabled !== previous.enabled || state.product !== previous.product
        || state.points !== previous.points || state.results !== previous.results || state.mode !== previous.mode) redraw();
    }),
    useUiStore.subscribe((state, previous) => {
      if (state.forecastOutlinesOnly !== previous.forecastOutlinesOnly && active()) redraw();
    }),
    useMapStore.subscribe((state) => state.activePanel, update),
    useAppStore.subscribe((state) => state.activeWorkspace, () => { update(); if (useForecastDrawingStore.getState().enabled) redraw(); }),
  ];
  update();
  return () => {
    subscriptions.forEach((unsubscribe) => { unsubscribe(); });
    map.off("click", click);
    dragging.dispose();
    setDrawing(false);
    canvas.classList.remove(styles.moveMode ?? "");
    vertices([]);
    releaseBox();
  };
}

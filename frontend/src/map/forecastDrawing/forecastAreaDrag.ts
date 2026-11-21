import type { Map as MapLibreMap } from "maplibre-gl";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useMapStore } from "../../stores/mapStore";
import type { ForecastPoint, ForecastProduct } from "../../types/forecastDrawing";
import { forecastMapPoint, forecastRingContains, translateForecastRing } from "./forecastDrawingCoordinates";
import { forecastEditTargetAt } from "./forecastDrawingSelection";

interface ForecastDrag {
  pointer: number;
  gesture: symbol;
  id: string;
  product: ForecastProduct;
  points: readonly ForecastPoint[];
  origin: ForecastPoint;
  screen: ForecastPoint;
  pan: boolean;
  moved: boolean;
}

/** Translate the selected contour while Move area is active. */
export function wireForecastAreaDrag(map: MapLibreMap, panel: number, active: () => boolean): {
  sync(): void;
  dispose(): void;
} {
  const canvas = map.getCanvasContainer();
  let drag: ForecastDrag | null = null;
  const currentEdit = ({ gesture, id, product }: ForecastDrag): boolean => {
    const state = useForecastDrawingStore.getState();
    return state.mode === "edit" && !state.saving && state.product === product && state.selectedId === id
      && state.editGesture?.id === gesture;
  };
  const screenPoint = (event: PointerEvent) => {
    const bounds = canvas.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  };
  const stop = (cancel: boolean): void => {
    const ended = drag;
    if (ended === null) return;
    drag = null;
    delete canvas.dataset.forecastMoving;
    if (canvas.hasPointerCapture(ended.pointer)) canvas.releasePointerCapture(ended.pointer);
    if (ended.pan) map.dragPan.enable();
    useForecastDrawingStore.getState().endEditGesture(ended.gesture, cancel);
  };
  const down = (event: PointerEvent): void => {
    const state = useForecastDrawingStore.getState();
    if (!active() || drag !== null || state.saving || state.editTool !== "move" || !event.isPrimary
      || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
      || (event.target instanceof Element && event.target.closest("[data-forecast-handle]") !== null)) return;
    const screen = screenPoint(event);
    const point = forecastMapPoint(map, screen, map.unproject([screen.x, screen.y]));
    if (point === null) return;
    if (state.mode !== "edit" || !forecastRingContains(state.points, point)) {
      const { product, polygons, results } = state;
      const target = forecastEditTargetAt(point, product, polygons[product], results[product]);
      if (target === undefined || !state.startEdit(target)) return;
    }
    const edit = useForecastDrawingStore.getState();
    if (edit.selectedId === null) return;
    const gesture = edit.beginEditGesture();
    if (gesture === null) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    useMapStore.getState().setActivePanel(panel);
    map.getCanvas().focus({ preventScroll: true });
    drag = { pointer: event.pointerId, gesture, id: edit.selectedId, product: edit.product,
      points: edit.points, origin: point, screen: [screen.x, screen.y], pan: map.dragPan.isEnabled(), moved: false };
    map.dragPan.disable();
    canvas.setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent): void => {
    const moving = drag;
    if (moving === null || event.pointerId !== moving.pointer) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const screen = screenPoint(event);
    if (!moving.moved && Math.hypot(screen.x - moving.screen[0], screen.y - moving.screen[1]) < 3) return;
    const point = forecastMapPoint(map, screen, map.unproject([screen.x, screen.y]));
    if (point === null) return;
    moving.moved = true;
    canvas.dataset.forecastMoving = "true";
    useForecastDrawingStore.getState().replaceEditPoints(translateForecastRing(moving.points, moving.origin, point), moving.gesture);
  };
  const up = (event: PointerEvent): void => {
    if (event.pointerId !== drag?.pointer) return;
    move(event);
    stop(false);
  };
  const cancelPointer = (event: PointerEvent): void => { if (event.pointerId === drag?.pointer) stop(true); };
  const blur = (): void => { stop(true); };
  canvas.addEventListener("pointerdown", down, true);
  canvas.addEventListener("pointermove", move, true);
  canvas.addEventListener("pointerup", up, true);
  canvas.addEventListener("pointercancel", cancelPointer, true);
  canvas.addEventListener("lostpointercapture", cancelPointer, true);
  window.addEventListener("blur", blur);
  return {
    sync: () => {
      if (drag !== null && (!active() || !currentEdit(drag) || panel !== useMapStore.getState().activePanel
        || useForecastDrawingStore.getState().editTool !== "move")) stop(true);
    },
    dispose: () => {
      stop(true);
      canvas.removeEventListener("pointerdown", down, true);
      canvas.removeEventListener("pointermove", move, true);
      canvas.removeEventListener("pointerup", up, true);
      canvas.removeEventListener("pointercancel", cancelPointer, true);
      canvas.removeEventListener("lostpointercapture", cancelPointer, true);
      window.removeEventListener("blur", blur);
    },
  };
}

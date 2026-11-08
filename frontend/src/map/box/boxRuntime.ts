import type { FeatureCollection, Polygon } from "geojson";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import { useAppStore } from "../../stores/appStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { useMapStore } from "../../stores/mapStore";
import type { Bounds } from "../../types/soundings";
import { cssColour } from "../basemap/basemapLayers";
import { documentTokenReader, readCanvasTokens } from "../canvasTokens";
import { useHoverStore } from "../hover/hoverStore";
import { boxBetween, boxContains, boxRing, moveBox, type Position } from "./boxGeometry";
import { boxHandles } from "./boxHandles";

const SOURCE = "sounding-box";

/** Owns only box gestures and two selection layers; the camera/overlay remain native. */
export function wireBoxSelection(map: MapLibreMap, panel: number): () => void {
  const canvas = map.getCanvasContainer();
  const handles = boxHandles(map);
  let gesture: { id: number; start: Position; original: Bounds | null; move: boolean; pan: boolean } | null = null;
  let space = false;
  let styleReady = map.isStyleLoaded();
  const active = (): boolean => useBoxSoundingStore.getState().mode === "box"
    && useMapStore.getState().view === "map" && useAppStore.getState().activeWorkspace === "map";
  const render = (): void => {
    const state = useBoxSoundingStore.getState();
    const bounds = active() ? (state.editing ? state.draft : state.bounds) : null;
    if (!styleReady) return;
    if (map.getSource(SOURCE) === undefined) {
      const tokens = readCanvasTokens(documentTokenReader());
      map.addSource(SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: `${SOURCE}-fill`, type: "fill", source: SOURCE,
        paint: { "fill-color": cssColour(tokens.colours.selectedHalo) } });
      map.addLayer({ id: `${SOURCE}-line`, type: "line", source: SOURCE,
        paint: { "line-color": cssColour(tokens.colours.selected), "line-width": tokens.lengths.domainWidth } });
    }
    const data: FeatureCollection<Polygon> = { type: "FeatureCollection", features: bounds === null ? [] : [
      { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [boxRing(bounds)] } },
    ] };
    void (map.getSource(SOURCE) as GeoJSONSource).setData(data);
    // Only the active panel's corner controls are focusable.
    handles.update(useMapStore.getState().activePanel === panel ? bounds : null);
    if (gesture === null) canvas.style.cursor = active() ? (space ? "grab" : "crosshair") : "";
  };
  const coordinate = (event: PointerEvent): Position | null => {
    const rect = canvas.getBoundingClientRect();
    const screen: [number, number] = [event.clientX - rect.left, event.clientY - rect.top];
    const point = map.unproject(screen);
    const projected = map.project(point);
    // Off-globe sky unprojects to its horizon; refuse that clipped location.
    if (!Number.isFinite(point.lng) || !Number.isFinite(point.lat) || Math.abs(point.lat) >= 90
      || Math.hypot(projected.x - screen[0], projected.y - screen[1]) > 2) return null;
    return [point.lng, point.lat];
  };
  const down = (event: PointerEvent): void => {
    if (!active() || gesture !== null || space || event.button !== 0 || (event.target as Element).closest("[data-box-handle]") !== null) return;
    const point = coordinate(event);
    if (point === null) return;
    event.preventDefault(); event.stopImmediatePropagation();
    map.getCanvas().focus({ preventScroll: true });
    const state = useBoxSoundingStore.getState();
    gesture = { id: event.pointerId, start: point, original: state.bounds,
      move: state.bounds !== null && boxContains(state.bounds, point), pan: map.dragPan.isEnabled() };
    map.dragPan.disable();
    canvas.setPointerCapture(event.pointerId);
    useMapStore.getState().setActivePanel(panel);
    useHoverStore.getState().clear();
    state.begin();
    canvas.style.cursor = gesture.move ? "grabbing" : "crosshair";
  };
  const move = (event: PointerEvent): void => {
    if (gesture === null || gesture.id !== event.pointerId) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const point = coordinate(event);
    if (point === null) return;
    useBoxSoundingStore.getState().preview(gesture.move && gesture.original !== null
      ? moveBox(gesture.original, gesture.start, point) : boxBetween(gesture.start, point));
  };
  const finish = (event?: PointerEvent, cancel = false): void => {
    if (gesture === null || (event !== undefined && gesture.id !== event.pointerId)) return;
    event?.preventDefault(); event?.stopImmediatePropagation();
    const previous = gesture;
    gesture = null;
    if (canvas.hasPointerCapture(previous.id)) canvas.releasePointerCapture(previous.id);
    if (previous.pan) map.dragPan.enable();
    const state = useBoxSoundingStore.getState();
    if (!cancel && state.draft !== null) state.commit(state.draft); else state.cancelEdit();
    render();
  };
  const key = (event: KeyboardEvent): void => {
    if (!active()) return;
    if (event.code === "Space" && (event.target === map.getCanvas() || event.target === document.body)) {
      space = event.type === "keydown"; render();
    }
    if (event.type === "keydown" && event.key === "Escape" && (gesture !== null || useBoxSoundingStore.getState().editing)) {
      event.preventDefault(); event.stopPropagation(); cancel();
    }
  };
  const cancel = (): void => { space = false; finish(undefined, true); handles.cancel(); render(); };
  const cancelPointer = (event: PointerEvent): void => { finish(event, true); };
  canvas.addEventListener("pointerdown", down, true);
  canvas.addEventListener("pointermove", move, true);
  canvas.addEventListener("pointerup", finish, true);
  canvas.addEventListener("pointercancel", cancelPointer, true);
  canvas.addEventListener("lostpointercapture", cancelPointer, true);
  window.addEventListener("keydown", key);
  window.addEventListener("keyup", key);
  window.addEventListener("blur", cancel);
  const styleLoaded = (): void => { styleReady = true; render(); };
  const styleLoading = (): void => { styleReady = false; };
  map.on("style.load", styleLoaded);
  map.on("styledataloading", styleLoading);
  const unsubscribers = [useBoxSoundingStore.subscribe((state) => { if (state.mode !== "box") finish(undefined, true); render(); }),
    useMapStore.subscribe((state) => [state.view, state.activePanel].join(), render),
    useAppStore.subscribe((state) => state.activeWorkspace, () => { if (!active()) cancel(); render(); })];
  render();
  return () => {
    cancel(); unsubscribers.forEach((unsubscribe) => { unsubscribe(); }); handles.dispose();
    canvas.style.cursor = "";
    canvas.removeEventListener("pointerdown", down, true);
    canvas.removeEventListener("pointermove", move, true);
    canvas.removeEventListener("pointerup", finish, true);
    canvas.removeEventListener("pointercancel", cancelPointer, true);
    canvas.removeEventListener("lostpointercapture", cancelPointer, true);
    window.removeEventListener("keydown", key); window.removeEventListener("keyup", key); window.removeEventListener("blur", cancel);
    map.off("style.load", styleLoaded);
    map.off("styledataloading", styleLoading);
  };
}

import { Marker, type Map as MapLibreMap } from "maplibre-gl";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { MAX_FORECAST_VERTICES, type ForecastPoint } from "../../types/forecastDrawing";
import { wrapLongitude } from "../box/boxGeometry";
import styles from "./forecastDrawingRuntime.module.css";

const ARROW_STEPS: Readonly<Record<string, ForecastPoint>> = {
  ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
};
const MIN_HANDLE_SPACING_PX = 24;

/** Keyboard and pointer handles are only attached to the active map panel. */
export function forecastDrawingVertices(map: MapLibreMap): (points: readonly ForecastPoint[], saving?: boolean, generated?: boolean) => void {
  const markers: Marker[] = [];
  const midpoints: Marker[] = [];
  let dragging: Marker | null = null;
  let gesture: symbol | null = null;
  let decluttering = false;
  const endDrag = (cancel: boolean): void => {
    const id = gesture;
    dragging = null;
    gesture = null;
    window.removeEventListener("blur", cancelDrag);
    window.removeEventListener("touchcancel", cancelDrag);
    if (id !== null) useForecastDrawingStore.getState().endEditGesture(id, cancel);
  };
  const cancelDrag = (): void => {
    if (dragging === null) return;
    // Remove MapLibre's in-flight mouse and touch listeners after an interrupted drag.
    dragging.remove().addTo(map);
    dragging.getElement().style.pointerEvents = "";
    endDrag(true);
  };
  const spaceHandles = (): void => {
    const positions: { x: number; y: number }[] = [];
    const handles = [...markers, ...midpoints];
    if (dragging !== null) handles.unshift(...handles.splice(handles.indexOf(dragging), 1));
    for (const handle of handles) {
      const position = map.project(handle.getLngLat());
      const crowded = positions.some(({ x, y }) => Math.hypot(x - position.x, y - position.y) < MIN_HANDLE_SPACING_PX);
      const element = handle.getElement();
      element.classList.toggle(styles.crowded ?? "", crowded);
      if (!crowded && !element.classList.contains("maplibregl-marker-covered")) positions.push(position);
    }
  };
  const createHandleButton = (className: string | undefined, label: string): HTMLButtonElement => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className ?? "";
    button.dataset.forecastHandle = "true";
    button.setAttribute("aria-label", label);
    const stopClick = (event: MouseEvent): void => { event.stopPropagation(); };
    button.addEventListener("click", stopClick);
    button.addEventListener("dblclick", stopClick);
    button.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.altKey || event.ctrlKey || event.metaKey) return;
      event.stopPropagation();
      useForecastDrawingStore.getState().cancel();
      map.getCanvas().focus({ preventScroll: true });
    });
    return button;
  };
  return (points, saving = false, generated = false) => {
    if (generated !== decluttering) {
      decluttering = generated;
      if (generated) {
        map.on("move", spaceHandles);
      } else {
        map.off("move", spaceHandles);
        for (const handle of [...markers, ...midpoints]) handle.getElement().classList.remove(styles.crowded ?? "");
      }
    }
    markers.splice(points.length).forEach((marker) => {
      marker.remove();
      if (dragging === marker) endDrag(true);
    });
    points.forEach((point, index) => {
      let marker = markers[index];
      if (marker === undefined) {
        const button = createHandleButton(styles.vertex, `Forecast vertex ${String(index + 1)}`);
        button.setAttribute("aria-description", "Drag or use the arrow keys to move this vertex. Press Delete to remove it.");
        const handle = new Marker({ element: button, draggable: true }).setLngLat([...point]).addTo(map);
        marker = handle;
        markers.push(handle);
        const move = (id?: symbol): void => {
          const coord = handle.getLngLat().wrap();
          useForecastDrawingStore.getState().moveVertex(index, [coord.lng, coord.lat], id);
        };
        handle.on("dragstart", () => {
          if (dragging !== null) return;
          dragging = handle;
          gesture = useForecastDrawingStore.getState().beginEditGesture();
          if (gesture === null) { dragging = null; return; }
          window.addEventListener("blur", cancelDrag);
          window.addEventListener("touchcancel", cancelDrag);
        });
        handle.on("drag", () => { if (dragging === handle && gesture !== null) move(gesture); });
        handle.on("dragend", () => {
          if (dragging !== handle || gesture === null) return;
          move(gesture);
          endDrag(false);
        });
        button.addEventListener("keydown", (event) => {
          if (event.altKey || event.ctrlKey || event.metaKey || dragging !== null) return;
          if (event.key === "Delete") {
            event.preventDefault();
            event.stopPropagation();
            useForecastDrawingStore.getState().removeVertex(index);
            (markers[Math.min(index, markers.length - 1)]?.getElement() ?? map.getCanvas()).focus({ preventScroll: true });
            return;
          }
          const direction = ARROW_STEPS[event.key];
          if (direction === undefined) return;
          event.preventDefault();
          event.stopPropagation();
          const { x, y } = map.project(handle.getLngLat());
          const step = event.shiftKey ? 10 : 1;
          handle.setLngLat(map.unproject([x + direction[0] * step, y + direction[1] * step]));
          move();
        });
      }
      marker.setDraggable(!saving);
      (marker.getElement() as HTMLButtonElement).disabled = saving;
      if (marker !== dragging) marker.setLngLat([...point]);
    });

    const first = points[0];
    const showMidpoints = first !== undefined && points.length < MAX_FORECAST_VERTICES;
    midpoints.splice(showMidpoints ? points.length : 0).forEach((marker) => { marker.remove(); });
    if (!showMidpoints) {
      if (generated) spaceHandles();
      return;
    }
    points.forEach(([lng, lat], index) => {
      const [nextLng, nextLat] = points[index + 1] ?? first;
      const midpoint: [number, number] = [wrapLongitude(lng + wrapLongitude(nextLng - lng) / 2), (lat + nextLat) / 2];
      let marker = midpoints[index];
      if (marker === undefined) {
        const button = createHandleButton(styles.midpoint, `Add vertex on edge ${String(index + 1)}`);
        button.addEventListener("touchstart", (event) => { event.stopPropagation(); }, { passive: true });
        const handle = new Marker({ element: button }).setLngLat(midpoint).addTo(map);
        marker = handle;
        midpoints.push(handle);
        const insert = (): void => {
          const previousCount = markers.length;
          const coord = handle.getLngLat();
          useForecastDrawingStore.getState().insertVertex(index, [wrapLongitude(coord.lng), coord.lat]);
          if (markers.length > previousCount) markers[index + 1]?.getElement().focus({ preventScroll: true });
        };
        // MapLibre can consume a click while its drag handlers unwind. Keep the
        // pointer gesture on this handle and leave native click for keyboard use.
        let press: { id: number; x: number; y: number } | null = null;
        button.addEventListener("pointerdown", (event) => {
          if (button.disabled || !event.isPrimary || event.button !== 0) return;
          event.preventDefault();
          event.stopPropagation();
          press = { id: event.pointerId, x: event.clientX, y: event.clientY };
          button.setPointerCapture(event.pointerId);
        });
        button.addEventListener("pointerup", (event) => {
          const start = press;
          if (start?.id !== event.pointerId) return;
          press = null;
          event.stopPropagation();
          if (Math.hypot(event.clientX - start.x, event.clientY - start.y) <= 6) insert();
        });
        button.addEventListener("lostpointercapture", () => { press = null; });
        button.addEventListener("click", (event) => {
          if (event.detail === 0) insert();
        });
      } else {
        marker.setLngLat(midpoint);
      }
      (marker.getElement() as HTMLButtonElement).disabled = saving;
    });
    if (generated) spaceHandles();
  };
}

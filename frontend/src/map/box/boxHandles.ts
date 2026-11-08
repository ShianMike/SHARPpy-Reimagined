import { Marker, type Map as MapLibreMap } from "maplibre-gl";
import type { Bounds } from "../../types/soundings";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { resizeBox, type Position } from "./boxGeometry";
import styles from "./BoxSelection.module.css";

const corners = (box: Bounds): Position[] => [[box.west, box.south], [box.east, box.south], [box.east, box.north], [box.west, box.north]];

/** Custom markers own keyboard behavior; MapLibre handles pointer dragging. */
export function boxHandles(map: MapLibreMap): { update: (bounds: Bounds | null) => void; cancel: () => void; dispose: () => void } {
  const markers: Marker[] = [];
  let anchor: Position | undefined;
  let dragging: Marker | null = null;
  const update = (box: Bounds | null): void => {
    if (box === null) {
      const interrupted = dragging !== null;
      dragging = null;
      anchor = undefined;
      markers.splice(0).forEach((marker) => { marker.remove(); });
      if (interrupted) useBoxSoundingStore.getState().cancelEdit();
      return;
    }
    corners(box).forEach((position, index) => {
      let marker = markers[index];
      if (marker === undefined) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = styles.handle ?? "";
        button.dataset.boxHandle = "true";
        button.dataset.corner = String(index);
        button.setAttribute("aria-label", `Resize box ${["southwest", "southeast", "northeast", "northwest"][index] ?? "corner"}`);
        button.setAttribute("aria-description", "Drag, or use arrow keys. Hold Shift for larger steps.");
        marker = new Marker({ element: button, draggable: true }).setLngLat([...position]).addTo(map);
        const current = marker;
        const begin = (): void => {
          const bounds = useBoxSoundingStore.getState().bounds;
          if (bounds === null || dragging !== null || !markers.includes(current)) return;
          dragging = current;
          anchor = corners(bounds)[(index + 2) % 4];
          useBoxSoundingStore.getState().begin();
        };
        const preview = (): void => {
          if (dragging !== current || anchor === undefined) return;
          const point = current.getLngLat();
          useBoxSoundingStore.getState().preview(resizeBox(anchor, index, [point.lng, point.lat]));
        };
        const finish = (): void => {
          if (dragging !== current) return;
          dragging = null;
          anchor = undefined;
          const state = useBoxSoundingStore.getState();
          if (state.draft !== null) state.commit(state.draft); else state.cancelEdit();
        };
        marker.on("dragstart", begin);
        marker.on("drag", preview);
        marker.on("dragend", finish);
        button.addEventListener("keydown", (event) => {
          if (event.altKey || event.ctrlKey || event.metaKey || dragging !== null) return;
          const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
          if (delta === undefined) return;
          event.preventDefault(); event.stopPropagation();
          begin();
          const point = map.project(current.getLngLat());
          const amount = event.shiftKey ? 10 : 1;
          current.setLngLat(map.unproject([point.x + (delta[0] ?? 0) * amount, point.y + (delta[1] ?? 0) * amount]));
          preview(); finish();
        });
        markers.push(marker);
      }
      marker.setLngLat([...position]);
    });
  };
  return { update, cancel: () => { if (dragging !== null) update(null); }, dispose: () => { update(null); } };
}

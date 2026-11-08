// The map toolbar (DESIGN-frontend F8): Zoom in, Zoom out, and Reset view in
// one glass group, in the overlay's left column, then the panel layout: one
// map, two, or four synchronized panels (REQUEST §14). The camera actions come
// from the runtime, so this component holds no map logic.

import { Columns2, Grid2x2, MapPin, Minus, Plus, Scan, Square, SquareDashedMousePointer, type LucideIcon } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { Surface } from "../../ui/Surface";
import { IconAction } from "../../ui/Controls";
import { useMapStore, type PanelLayout } from "../../stores/mapStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { useForecastStore } from "../../stores/forecastStore";
import styles from "./MapToolbar.module.css";

export interface MapToolbarProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetView: () => void;
  onLayoutChange: (layout: PanelLayout) => void;
}

const LAYOUTS: readonly { layout: PanelLayout; icon: LucideIcon; label: string }[] = [
  { layout: 1, icon: Square, label: "One map" },
  { layout: 2, icon: Columns2, label: "Two synchronized panels" },
  { layout: 4, icon: Grid2x2, label: "Four synchronized panels" },
];

export function MapToolbar({ onZoomIn, onZoomOut, onResetView, onLayoutChange }: MapToolbarProps): ReactNode {
  const current = useMapStore((state) => state.panelLayout);
  const live = useMapStore((state) => state.view === "live");
  const mode = useBoxSoundingStore((state) => state.mode);
  return (
    <Surface role="toolbar" aria-label="Map" aria-orientation="vertical" elevation="floating" className={styles.toolbar}>
      <IconAction icon={Plus} label="Zoom in" hintSide="right" onClick={onZoomIn} />
      <IconAction icon={Minus} label="Zoom out" hintSide="right" onClick={onZoomOut} />
      <IconAction icon={Scan} label="Reset view" hintSide="right" onClick={onResetView} />
      <span className={styles.divider} aria-hidden="true" />
      <div className={styles.layouts} role="group" aria-label="Map layout" data-selection-track="vertical"
        style={{ "--selection-index": LAYOUTS.findIndex(({ layout }) => layout === current) } as CSSProperties}>
        {LAYOUTS.map(({ layout, icon, label }) => <IconAction key={layout} icon={icon} label={label} hintSide="right"
          aria-pressed={current === layout} onClick={() => { onLayoutChange(layout); }} />)}
      </div>
      {live ? null : <>
        <span className={styles.divider} aria-hidden="true" />
        <div className={styles.layouts} role="group" aria-label="Sounding selection" data-selection-track="vertical"
          style={{ "--selection-index": mode === "point" ? 0 : 1 } as CSSProperties}>
          <IconAction icon={MapPin} label="Point sounding" hintSide="right" aria-pressed={mode === "point"}
            onClick={() => { useBoxSoundingStore.getState().setMode("point"); }} />
          <IconAction icon={SquareDashedMousePointer} label="Box sounding" hintSide="right" aria-pressed={mode === "box"}
            onClick={() => { useForecastStore.getState().setPlaying(false); useBoxSoundingStore.getState().setMode("box"); }} />
        </div>
      </>}
    </Surface>
  );
}

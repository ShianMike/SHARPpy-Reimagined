// The map toolbar (DESIGN-frontend F8): Zoom in, Zoom out, and Reset view in
// one glass group, in the overlay's left column, then the panel layout: one
// map, two, or four synchronized panels (REQUEST §14). The camera actions come
// from the runtime, so this component holds no map logic.

import { Columns2, Grid2x2, Minus, Plus, Scan, Square, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Surface } from "../../ui/Surface";
import { IconAction } from "../../ui/Controls";
import { useMapStore, type PanelLayout } from "../../stores/mapStore";
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
  return (
    <Surface role="toolbar" aria-label="Map" aria-orientation="vertical" elevation="floating" className={styles.toolbar}>
      <IconAction icon={Plus} label="Zoom in" hintSide="right" onClick={onZoomIn} />
      <IconAction icon={Minus} label="Zoom out" hintSide="right" onClick={onZoomOut} />
      <IconAction icon={Scan} label="Reset view" hintSide="right" onClick={onResetView} />
      <span className={styles.divider} aria-hidden="true" />
      {LAYOUTS.map(({ layout, icon, label }) => (
        <IconAction
          key={layout}
          icon={icon}
          label={label}
          hintSide="right"
          aria-pressed={current === layout}
          onClick={() => {
            onLayoutChange(layout);
          }}
        />
      ))}
    </Surface>
  );
}

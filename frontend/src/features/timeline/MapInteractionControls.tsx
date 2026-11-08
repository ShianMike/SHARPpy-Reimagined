import type { CSSProperties, ReactNode } from "react";
import { Hand, MousePointer2 } from "lucide-react";
import { useHoverStore } from "../../map/hover/hoverStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { useMapStore } from "../../stores/mapStore";
import { IconAction } from "../../ui/Controls";
import styles from "./TimelineRegion.module.css";

export function MapInteractionControls(): ReactNode {
  const inspecting = useHoverStore((state) => state.inspecting);
  const box = useBoxSoundingStore((state) => state.mode === "box");
  const live = useMapStore((state) => state.view === "live");
  const selectingBox = box && !live;
  return <div className={styles.interaction} role="group" aria-label="Map interaction" data-selection-track={selectingBox ? undefined : "horizontal"}
    style={{ "--selection-index": Number(inspecting) } as CSSProperties}>
    {([false, true] as const).map((value) => {
      const label = value ? "Inspect" : "Navigate";
      const description = value ? "Show map hover readouts" : "Pan and zoom without hover readouts";
      return <IconAction key={label} icon={value ? MousePointer2 : Hand} size="sm" label={label} hintSide="top"
        aria-description={description} aria-pressed={!selectingBox && inspecting === value}
        onClick={() => { if (selectingBox) useBoxSoundingStore.getState().setMode("point"); useHoverStore.getState().setInspecting(value); }} />;
    })}
  </div>;
}

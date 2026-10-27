import type { ReactNode } from "react";
import { Hand, MousePointer2 } from "lucide-react";
import { useHoverStore } from "../../map/hover/hoverStore";
import { IconAction } from "../../ui/Controls";
import styles from "./TimelineRegion.module.css";

export function MapInteractionControls(): ReactNode {
  const inspecting = useHoverStore((state) => state.inspecting);
  return <div className={styles.interaction} role="group" aria-label="Map interaction">
    {([false, true] as const).map((value) => {
      const label = value ? "Inspect" : "Navigate";
      const description = value ? "Show map hover readouts" : "Pan and zoom without hover readouts";
      return <IconAction key={label} icon={value ? MousePointer2 : Hand} size="sm" label={label} hintSide="top"
        aria-description={description} aria-pressed={inspecting === value}
        onClick={() => { useHoverStore.getState().setInspecting(value); }} />;
    })}
  </div>;
}

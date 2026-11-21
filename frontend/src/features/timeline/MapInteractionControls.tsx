import type { CSSProperties, ReactNode } from "react";
import { Hand, MousePointer2, PenTool } from "lucide-react";
import { useHoverStore } from "../../map/hover/hoverStore";
import { useBoxSoundingStore } from "../../stores/boxSoundingStore";
import { useForecastDrawingStore } from "../../stores/forecastDrawingStore";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { useMapStore } from "../../stores/mapStore";
import { IconAction } from "../../ui/Controls";
import styles from "./TimelineRegion.module.css";

export function MapInteractionControls(): ReactNode {
  const inspecting = useHoverStore((state) => state.inspecting);
  const box = useBoxSoundingStore((state) => state.mode === "box");
  const live = useMapStore((state) => state.view === "live");
  const drawing = useForecastDrawingStore((state) => !live && state.enabled);
  const saving = useForecastDrawingStore((state) => !live && state.saving);
  const mapAvailable = useGraphicsStore((state) => state.effective.mode === "full" && state.renderer !== "failed" && !state.contextLost);
  const selectingBox = box && !live && !drawing;
  const drawUnavailable = !mapAvailable && !drawing;
  return <div className={styles.interaction} role="group" aria-label="Map interaction" data-selection-track={selectingBox ? undefined : "horizontal"}
    style={{ "--selection-index": drawing ? 2 : Number(inspecting) } as CSSProperties}>
    {([false, true] as const).map((value) => {
      const label = value ? "Inspect" : "Navigate";
      const description = value ? "Show map hover readouts" : "Pan and zoom without hover readouts";
      return <IconAction key={label} icon={value ? MousePointer2 : Hand} size="sm" label={label} hintSide="top"
        aria-description={description} aria-pressed={!drawing && !selectingBox && inspecting === value} disabled={saving}
        onClick={() => {
          useForecastDrawingStore.getState().setEnabled(false);
          if (selectingBox) useBoxSoundingStore.getState().setMode("point");
          useHoverStore.getState().setInspecting(value);
        }} />;
    })}
    {live ? null : <IconAction className={styles.drawForecast} icon={PenTool} size="sm" label="Draw Forecast" hintSide="top"
      hintText={drawUnavailable ? "Draw Forecast needs an interactive map" : "Draw Forecast"}
      aria-description={drawUnavailable ? "Draw Forecast needs an interactive map." : undefined}
      aria-pressed={drawing} disabled={drawUnavailable || saving} onClick={() => { useForecastDrawingStore.getState().setEnabled(!drawing); }} />}
  </div>;
}

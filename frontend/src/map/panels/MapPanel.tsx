// One map panel (REQUEST §14): its container and runtime, the data it draws,
// and its chip, scale and cursor value. With two or four panels the chip makes
// the panel the one the layer panel edits.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useMapStore, type PanelLayout } from "../../stores/mapStore";
import { forecastLabel } from "../../features/sounding/soundingTime";
import { ParameterScale } from "../controls/ParameterScale";
import { FieldTooltip } from "../hover/FieldTooltip";
import type { PanelField } from "../panelField";
import { usePanelScene } from "../panelScene";
import { MapRuntime } from "../runtime/MapRuntime";
import styles from "./MapPanel.module.css";

export const MAP_HINT_ID = "map-keyboard-hint";

/** The chip's status line for the panel's field. */
export function fieldStatus(field: PanelField): string | null {
  switch (field.blocked) {
    case "hidden":
      return "Model field off";
    case "no-catalog":
      return field.catalog.isError ? "Map products could not be loaded" : "Loading map products";
    case "loading-models":
      return "Loading the forecast models";
    case "other-model":
      return "Map fields are not available for this model";
    case null:
      break;
  }
  const wanted = field.request?.forecastHour ?? 0;
  const shown = field.frame.data;
  if (field.frame.isError) {
    return `${forecastLabel(wanted)} could not be loaded`;
  }
  if (shown === undefined) {
    return `Loading ${forecastLabel(wanted)}`;
  }
  return shown.forecastHour === wanted ? null : `Loading ${forecastLabel(wanted)}`;
}

function PanelChip({ panel, layout, field }: { panel: number; layout: PanelLayout; field: PanelField }): ReactNode {
  const active = useMapStore((state) => state.activePanel === panel);
  const name = field.product?.label ?? "Model field";
  const status = fieldStatus(field);
  if (layout === 1) {
    return null;
  }
  const body = (
    <>
      <span className={styles.product}>{`${String(panel + 1)}. ${name}`}</span>
      {status === null ? null : <span className={cx(styles.status, "tabular")}>{status}</span>}
    </>
  );
  return (
    <button
      type="button"
      className={styles.chip}
      data-motion="fade"
      aria-pressed={active}
      aria-label={`Panel ${String(panel + 1)}: ${name}. Edit its layers.`}
      data-testid={`panel-chip-${String(panel + 1)}`}
      onClick={() => {
        useMapStore.getState().setActivePanel(panel);
      }}
    >
      {body}
    </button>
  );
}

export interface MapPanelProps {
  panel: number;
  layout: PanelLayout;
  /** Panel 0 hands its runtime up, for the map toolbar's actions. */
  onRuntime?: ((runtime: MapRuntime | null) => void) | undefined;
}

export function MapPanel({ panel, layout, onRuntime }: MapPanelProps): ReactNode {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [runtime, setRuntime] = useState<MapRuntime | null>(null);
  const active = useMapStore((state) => state.activePanel === panel);
  const field = usePanelScene(runtime, panel);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) {
      return;
    }
    const created = new MapRuntime(container, panel);
    setRuntime(created);
    onRuntime?.(created);
    return () => {
      created.dispose();
      onRuntime?.(null);
    };
  }, [panel, onRuntime]);

  return (
    <div
      className={styles.panel}
      data-active={layout > 1 && active}
      data-panel={panel + 1}
      data-testid={`map-panel-${String(panel + 1)}`}
    >
      <div
        ref={containerRef}
        className={styles.map}
        aria-describedby={MAP_HINT_ID}
        data-testid={panel === 0 ? "map" : undefined}
        onFocus={() => {
          // Clicking or tabbing into a panel makes it the one the layer panel edits.
          useMapStore.getState().setActivePanel(panel);
        }}
      />
      <ParameterScale panel={panel} field={field} />
      <PanelChip panel={panel} layout={layout} field={field} />
      <FieldTooltip panel={panel} field={field} />
    </div>
  );
}

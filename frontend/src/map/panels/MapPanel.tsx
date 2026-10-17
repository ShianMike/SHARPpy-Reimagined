// One map panel (REQUEST §14): its container and runtime, the data it draws,
// and its chip, scale and cursor value. With two or four panels the chip opens
// the parameter picker for that panel.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { useAppStore } from "../../stores/appStore";
import { useMapStore, type PanelLayout } from "../../stores/mapStore";
import { forecastLabel } from "../../features/sounding/soundingTime";
import { useForecastContext } from "../../features/forecast/useForecast";
import { ParameterPicker } from "../../features/toolbar/ParameterPicker";
import { Popover } from "../../components/Popover";
import pickerStyles from "../../features/toolbar/ForecastControls.module.css";
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
  const context = useForecastContext();
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const name = field.product?.label ?? "Model field";
  const status = fieldStatus(field);
  if (layout === 1) {
    return null;
  }
  const body = (
    <>
      <span className={styles.product}>{`${String(panel + 1)} • ${name}`}</span>
      {status === null ? null : <span className={cx(styles.status, "tabular")}>{status}</span>}
    </>
  );
  return (
    <><button
      type="button"
      className={styles.chip}
      data-motion="fade"
      aria-pressed={active}
      aria-label={`Panel ${String(panel + 1)}: ${name}. Choose parameter.`}
      aria-haspopup="dialog"
      aria-expanded={anchor !== null}
      disabled={field.catalog.data === undefined || context?.fieldsAvailable !== true}
      data-testid={`panel-chip-${String(panel + 1)}`}
      onClick={(event) => {
        useMapStore.getState().setActivePanel(panel);
        setAnchor(anchor === null ? event.currentTarget : null);
      }}
    >
      {body}
    </button>
      <Popover id={`panel-${String(panel)}-parameters`} open={anchor !== null} movable anchor={anchor} onClose={() => { setAnchor(null); }} role="dialog" label="Parameters" initialFocus="first" align="start" width="content" className={pickerStyles.parameterPicker}>
        {field.catalog.data === undefined ? null : <ParameterPicker products={field.catalog.data.products} selected={field.product?.key ?? null} anchor={anchor} onClose={() => { setAnchor(null); }} />}
      </Popover>
    </>
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
  const isMap = useAppStore((state) => state.activeWorkspace === "map");
  const active = useMapStore((state) => state.activePanel === panel);
  const projection = useMapStore((state) => state.projection);
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
  }, [panel, onRuntime, projection]);

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
      {isMap ? <PanelChip panel={panel} layout={layout} field={field} /> : null}
      <FieldTooltip panel={panel} field={field} />
    </div>
  );
}

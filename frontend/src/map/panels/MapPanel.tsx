// One map panel (REQUEST §14): its container and runtime, the data it draws,
// and its chip, scale and cursor value. With two or four panels the chip opens
// the parameter picker for that panel.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { Notice } from "../../components/Notice";
import { Action } from "../../ui/Controls";
import { useGraphicsStore } from "../../stores/graphicsStore";
import { useAppStore } from "../../stores/appStore";
import { mapProjection, panelLayers, panelWeather, useMapStore, type PanelLayout } from "../../stores/mapStore";
import { RadarScale } from "../../features/tracking/RadarScale";
import { useMapCatalog } from "../mapQueries";
import { forecastLabel } from "../../features/sounding/soundingTime";
import { useForecastContext } from "../../features/forecast/useForecast";
import { ParameterPicker } from "../../features/toolbar/ParameterPicker";
import { Popover } from "../../components/Popover";
import pickerStyles from "../../features/toolbar/ForecastControls.module.css";
import { ParameterScale } from "../controls/ParameterScale";
import { FieldTooltip } from "../hover/FieldTooltip";
import { RadarTooltip } from "../hover/RadarTooltip";
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

function LivePanelChip({ panel, layout }: { panel: number; layout: PanelLayout }): ReactNode {
  const active = useMapStore((state) => state.activePanel === panel);
  const layers = useMapStore((state) => panelLayers(state, panel));
  const options = useMapStore((state) => panelWeather(state, panel));
  const catalog = useMapCatalog().data;
  const products = options.radarScope === "site" ? catalog?.siteProducts : catalog?.radarProducts;
  const product = products?.find((item) => item.key === options.radarProduct) ?? products?.[0];
  const title = layers.radar.visible ? product?.label ?? "Radar" : layers.satellite.visible ? `GOES ${options.satelliteChannel}` : "Live layers";
  if (layout === 1) return null;
  return <button type="button" className={styles.chip} aria-pressed={active} aria-label={`Panel ${String(panel + 1)}: ${title}. Select panel.`} data-testid={`panel-chip-${String(panel + 1)}`} onClick={() => {
    useMapStore.getState().setActivePanel(panel);
  }}><span className={styles.product}>{String(panel + 1)} • {title}</span></button>;
}

export function MapPanel({ panel, layout, onRuntime }: MapPanelProps): ReactNode {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [runtime, setRuntime] = useState<MapRuntime | null>(null);
  const isMap = useAppStore((state) => state.activeWorkspace === "map");
  const isLive = useAppStore((state) => state.activeWorkspace === "live");
  const live = useMapStore((state) => state.view === "live");
  const active = useMapStore((state) => state.activePanel === panel);
  const projection = useMapStore(mapProjection);
  const field = usePanelScene(runtime, panel);
  const rendererError = useGraphicsStore((state) => state.panelErrors[panel]);

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
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.repeat || !isMap || live || event.target instanceof HTMLElement && event.target.tagName !== "CANVAS") return;
          const center = runtime?.map?.getCenter();
          if (center === undefined) return;
          event.preventDefault();
          useAppStore.getState().selectPoint({ latitude: center.lat, longitude: center.lng });
        }}
        onFocus={() => {
          // Clicking or tabbing into a panel makes it the one the layer panel edits.
          useMapStore.getState().setActivePanel(panel);
        }}
      />
      {live ? <RadarScale panel={panel} /> : <ParameterScale panel={panel} field={field} />}
      {live && isLive ? <LivePanelChip panel={panel} layout={layout} /> : null}
      {isMap ? <PanelChip panel={panel} layout={layout} field={field} /> : null}
      {live ? isLive ? <RadarTooltip panel={panel} /> : null : <FieldTooltip panel={panel} field={field} />}
      {rendererError === undefined ? null : <div className={styles.notice}>
        <Notice tone="danger" surface="floating" role="alert" title={`Panel ${String(panel + 1)} could not be drawn`}
          message="Reload the maps to restore this panel. Your selected products and view are kept."
          actions={<Action onClick={() => { useGraphicsStore.getState().retryMap(); }}>Reload maps</Action>} />
      </div>}
      {rendererError !== undefined || field.issues.length === 0 ? null : <div className={styles.notice}>
        <Notice tone="warning" surface="floating" role="alert" title={`Panel ${String(panel + 1)} has incomplete layers`}
          message={field.issues.join(" ")} actions={<Action onClick={field.retry}>Retry layers</Action>} />
      </div>}
    </div>
  );
}

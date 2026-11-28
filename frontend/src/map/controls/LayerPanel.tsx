// Enabled weather layers lead in their draw order; the remaining layers can
// be added below. Settings and live provider controls edit the active panel.

import { ArrowLeft, CloudSun, EyeOff, List, Map as MapIcon, Settings2, X } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useForecastContext } from "../../features/forecast/useForecast";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { compactUtc, formatUtc, forecastLabel } from "../../features/sounding/soundingTime";
import { mapProjection, panelLayers, useMapStore } from "../../stores/mapStore";
import { resetLayerSettings } from "../../stores/preferences";
import type { StationAvailability } from "../../types/contracts";
import { Action, IconAction } from "../../ui/Controls";
import { LAYER_REGISTRY } from "../layers/descriptors";
import { useMapCatalog } from "../mapQueries";
import { layerInView, ORDERABLE_LAYER_IDS, type LayerDescriptor, type OrderableLayerId } from "../layers/registry";
import { WeatherAlertStatus } from "./WeatherAlertStatus";
import { AvailabilityControl } from "./AvailabilityControl";
import { FieldControls } from "./FieldControls";
import { LayerRow } from "./LayerRow";
import { WeatherLegend } from "./WeatherLegend";
import { DomainStatus, OutlookStatus, PlaceStatus, RadarControls, SatelliteControls } from "./OverlayControls";
import { WpcOutlookControls } from "./WpcOutlookControls";
import { ReportControls } from "./ReportControls";
import { SurfacePlotControls } from "./SurfacePlotControls";
import { SpotterNetworkStatus } from "./SpotterNetworkStatus";
import { spotterNetworkAvailable } from "../spotterQueries";
import { OpacityControl } from "./OpacityControl";
import styles from "./LayerPanel.module.css";

export interface LayerPanelProps {
  availabilities: ReadonlySet<StationAvailability>;
}

const ORDERABLE = new Set<string>(ORDERABLE_LAYER_IDS);
const CATEGORIES = [
  { id: "weather", label: "Weather", icon: CloudSun },
  { id: "legend", label: "Legend", icon: List },
  { id: "map", label: "Map", icon: MapIcon },
] as const;
const PROJECTIONS = ["mercator", "globe"] as const;
const descriptor = (id: string): LayerDescriptor | undefined => LAYER_REGISTRY.find((item) => item.id === id);
/** The map layers, in the order the panel lists them. */
const MAP_LAYERS = ["model-domain", "place-labels", "roads", "rivers", "radar-site-labels", "stations", "station-labels", "basemap"]
  .map(descriptor)
  .filter((item): item is LayerDescriptor => item !== undefined && item.userControllable);

/** The map layers' own controls and status, shown while the layer is on. */
const MAP_EXTRA: Readonly<Partial<Record<string, ReactNode>>> = {
  stations: <AvailabilityControl />,
  "model-domain": <DomainStatus />,
};

function WeatherExtra({ id, panel }: { id: OrderableLayerId; panel: number }): ReactNode {
  switch (id) {
    case "warnings":
    case "watches":
      return <WeatherAlertStatus kind={id === "warnings" ? "warning" : "watch"} />;
    case "model-field":
      return <FieldControls panel={panel} />;
    case "isobars":
      return <p className={styles.hint}>Mean sea-level pressure · 4 hPa intervals. Follows the selected model, run and forecast hour.</p>;
    case "satellite":
      return <SatelliteControls />;
    case "radar":
      return <RadarControls />;
    case "storm-reports":
      return <ReportControls />;
    case "surface-plots":
      return <SurfacePlotControls />;
    case "spotter-network":
      return <SpotterNetworkStatus />;
    case "wpc-ero":
      return <WpcOutlookControls panel={panel} kind="ero" />;
    case "wpc-winter":
      return <WpcOutlookControls panel={panel} kind="winter" />;
    default:
      return <OutlookStatus panel={panel} product={id.slice("spc-".length) as "cat" | "torn" | "wind" | "hail"} />;
  }
}

export function LayerPanel({ availabilities }: LayerPanelProps): ReactNode {
  const [expandedWeather, setExpandedWeather] = useState<{ panel: number; id: OrderableLayerId } | null>(null);
  const root = useRef<HTMLElement>(null);
  const focusAfterEdit = useRef<"heading" | OrderableLayerId | null>(null);
  const editorId = useId();
  const group = useMapStore((state) => state.layerPanelGroup);
  const projection = useMapStore(mapProjection);
  const live = useMapStore((state) => state.view === "live");
  const validTime = useWeatherTime();
  const [reset, setReset] = useState(false);
  const panel = useMapStore((state) => state.activePanel);
  const layout = useMapStore((state) => state.panelLayout);
  const states = useMapStore((state) => panelLayers(state, panel));
  const order = useMapStore((state) => state.layerOrder);
  const context = useForecastContext();
  const catalog = useMapCatalog().data;
  const isobarsUnavailable = !live && states.isobars.visible && context !== null && catalog !== undefined
    && (!context.fieldsAvailable || !catalog.products.some((product) => product.key === "mslp"));
  const showSpotters = live && spotterNetworkAvailable();
  const topFirst = [...order].reverse().filter((id) =>
    ORDERABLE.has(id) && layerInView(id, live ? "live" : "map") && (id !== "spotter-network" || showSpotters));
  const active = topFirst.filter((id) => states[id].visible);
  const available = topFirst.filter((id) => !states[id].visible);
  const mapDetailLayers = MAP_LAYERS.filter((layer) => layerInView(layer.id, live ? "live" : "map") && (!live || layer.id !== "model-domain"));
  const enabledMapDetails = mapDetailLayers.filter((layer) => states[layer.id].visible).length;
  const selectedId = expandedWeather?.panel === panel && active.includes(expandedWeather.id) ? expandedWeather.id : null;
  const selectedLayer = selectedId === null ? undefined : descriptor(selectedId);

  useLayoutEffect(() => {
    const focus = focusAfterEdit.current;
    if (focus === null) return;
    focusAfterEdit.current = null;
    // On compact layouts the other view only becomes focusable after the swap.
    if (focus === "heading") document.getElementById(`${editorId}-heading`)?.focus({ preventScroll: true });
    else root.current?.querySelector<HTMLElement>(`[data-testid="layer-row-${focus}"] [aria-expanded]`)?.focus();
  });

  const closeEditor = (id: OrderableLayerId): void => {
    focusAfterEdit.current = id;
    setExpandedWeather(null);
  };

  return (
    <section ref={root} aria-label={layout === 1 ? "Layers" : `Layers, panel ${String(panel + 1)}`} className={styles.panel} data-testid="layer-panel">
      <div className={styles.context}>
        <div className={styles.contextHeading}>
          <strong>{live ? "Live Tracking" : context === null ? "Weather overlays" : `${context.model.label} ${forecastLabel(context.hour)}`}</strong>
          <span className={styles.dataMode} data-fixture={import.meta.env.MODE === "fixture"} data-testid="layer-data-mode">
            {import.meta.env.MODE === "fixture" ? "Test data · synthetic weather layers" : "Live providers"}
          </span>
        </div>
        {live ? <span className={styles.hint}>Viewing · {formatUtc(validTime)} UTC</span> : context === null ? null : <span className={styles.hint}>Forecast valid · {compactUtc(context.validTime)} UTC</span>}
        {isobarsUnavailable ? <span className={styles.hint}>Isobars are unavailable for this model and forecast time.</span> : null}
      </div>
      <div className={styles.categories}>
        <div className={styles.groups} role="group" aria-label="Layer categories">
          {CATEGORIES.map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-pressed={group === id}
            aria-label={id === "map" ? "Map details" : undefined} onClick={() => { useMapStore.getState().setLayerPanelGroup(id); }}>
            <Icon className="icon" aria-hidden="true" /> {label}
          </button>)}
        </div>
        {layout === 1 ? null : <div className={styles.panelScope}>
          <span className={styles.panelLabel} aria-hidden="true">Panel</span>
          <div className={styles.panelPicker} role="group" aria-label="Active map panel"
            data-selection-track="horizontal" style={{ "--selection-index": panel } as CSSProperties}>
            {Array.from({ length: layout }, (_, index) => <button key={index} type="button" aria-pressed={panel === index}
              aria-label={`Panel ${String(index + 1)}`} onClick={() => { useMapStore.getState().setActivePanel(index); }}>{index + 1}</button>)}
          </div>
        </div>}
      </div>
      <div className={styles.body}>
        {group !== "weather" ? null : <div className={styles.weatherLayout} data-editing={selectedLayer !== undefined}>
          <div className={styles.layerStack} data-motion="fade">
          <section className={styles.layerColumn} aria-label="Active weather layers">
          <div className={styles.stackHeading}><h3 className={styles.group}>On map <span>{active.length} · top first</span></h3>
            <IconAction icon={EyeOff} size="sm" label="Hide all weather layers" disabled={active.length === 0}
              onClick={() => { setExpandedWeather(null); for (const id of active) useMapStore.getState().setLayerVisible(id, false); }} />
          </div>
          <div className={styles.layerList}>
          {active.length === 0 ? <p className={styles.hint}>Choose a layer from Add layers.</p> : null}
          {active.map((id, index) => {
            const layer = descriptor(id);
            return layer === undefined ? null : (
                <LayerRow key={id} variant="stack" layer={layer} state={states[id]} order={{ id, fromTop: index, count: active.length }}
                  disclosure={{ targetId: editorId, expanded: selectedId === id, onChange: (expanded) => {
                    if (expanded) focusAfterEdit.current = "heading";
                    setExpandedWeather((current) => expanded ? { panel, id } : current?.panel === panel && current.id === id ? null : current);
                  } }} />
            );
          })}
          </div></section>
          <section className={styles.layerColumn} aria-label="Available weather layers">
            <h3 className={styles.group}>Add layers <span>{available.length} available</span></h3>
            <div className={styles.layerList}>{available.map((id) => {
              const layer = descriptor(id);
              return layer === undefined ? null : <LayerRow key={id} variant="stack" layer={layer} state={states[id]} />;
            })}{available.length === 0 ? <p className={styles.hint}>All weather layers are on the map.</p> : null}</div>
          </section>
          </div>
          <section id={editorId} className={styles.layerEditor} aria-label="Layer settings">
            {selectedLayer === undefined || selectedId === null ? <div className={styles.editorEmpty} data-motion="fade">
              <span className={styles.editorEmptyIcon} aria-hidden="true"><Settings2 className="icon" /></span>
              <h3>Choose a layer</h3>
              <p>Select an on-map layer to adjust its settings.</p>
            </div> : <div key={selectedId} className={styles.editorContent} data-motion="enter">
              <header className={styles.editorHeading}>
                <Action className={styles.editorBack} onClick={() => { closeEditor(selectedId); }}><ArrowLeft className="icon-sm" aria-hidden="true" />Layers</Action>
                <span className={styles.editorDot} aria-hidden="true" />
                <h3 id={`${editorId}-heading`} tabIndex={-1}>{selectedLayer.label}</h3>
                <IconAction className={styles.editorClose} icon={X} size="sm" label="Close layer settings" onClick={() => { closeEditor(selectedId); }} />
              </header>
              <div className={styles.editorBody} data-testid={`layer-settings-${selectedId}`}>
                {selectedLayer.opacityControl ? <OpacityControl label={`${selectedLayer.label} opacity`} value={states[selectedId].opacity} onChange={(opacity) => { useMapStore.getState().setLayerOpacity(selectedId, opacity); }} /> : null}
                <WeatherExtra id={selectedId} panel={panel} />
              </div>
            </div>}
          </section>
        </div>}
        {group !== "legend" ? null : <div className={styles.scrollBody} data-motion="fade"><WeatherLegend active={active} panel={panel} availabilities={availabilities} /></div>}
        {group !== "map" ? null : <div className={styles.scrollBody} data-motion="fade">
          <div className={styles.projection}>
            <h3 className={styles.group}>Projection <span>{live ? "Flat map" : "All panels"}</span></h3>
            {live ? null : <div className={styles.panelPicker} role="group" aria-label="Map projection"
              data-selection-track="horizontal" style={{ "--selection-index": PROJECTIONS.indexOf(projection) } as CSSProperties}>
              {PROJECTIONS.map((value) => <button key={value} type="button" aria-pressed={projection === value}
                onClick={() => { useMapStore.getState().setProjection(value); }}>{value === "globe" ? "Globe" : "Flat map"}</button>)}
            </div>}
          </div>
          <h3 className={styles.group}>Map details <span>{`${String(enabledMapDetails)} of ${String(mapDetailLayers.length)} on`}</span></h3>
          <div className={styles.mapDetails}>{mapDetailLayers.map((layer) => (
            <LayerRow key={layer.id} variant="card" layer={layer} state={states[layer.id]}>
              {MAP_EXTRA[layer.id] ?? null}
            </LayerRow>
          ))}</div>
          {states["place-labels"].visible ? <PlaceStatus /> : null}
        </div>}
      </div>
      <footer className={styles.footer}>
        <span role="status" aria-label="Layer settings status">{reset ? live ? "Live layers reset" : "Layers reset · all panels" : "Saves automatically"}</span>
        <Action title={live ? "Reset live layers and weather options." : "Reset layers, projection and weather options in all panels. Keep the layout, parameters and map location."}
          onClick={() => { setExpandedWeather(null); resetLayerSettings(); setReset(true); }}>Reset layers</Action>
      </footer>
    </section>
  );
}

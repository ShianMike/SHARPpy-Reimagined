// Enabled weather layers lead in their draw order; the remaining layers can
// be added below. Settings and live provider controls edit the active panel.

import { CloudSun, EyeOff, List, Map as MapIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useForecastContext } from "../../features/forecast/useForecast";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { compactUtc, formatUtc, forecastLabel } from "../../features/sounding/soundingTime";
import { mapProjection, panelLayers, useMapStore } from "../../stores/mapStore";
import { resetLayerSettings } from "../../stores/preferences";
import type { StationAvailability } from "../../types/contracts";
import { Action, IconAction } from "../../ui/Controls";
import { LAYER_REGISTRY } from "../layers/descriptors";
import { layerInView, ORDERABLE_LAYER_IDS, type LayerDescriptor, type OrderableLayerId } from "../layers/registry";
import { WeatherAlertStatus } from "./WeatherAlertStatus";
import { AvailabilityControl } from "./AvailabilityControl";
import { FieldControls } from "./FieldControls";
import { LayerRow } from "./LayerRow";
import { WeatherLegend } from "./WeatherLegend";
import { DomainStatus, OutlookStatus, PlaceStatus, RadarControls, SatelliteControls } from "./OverlayControls";
import { ReportControls } from "./ReportControls";
import { SurfacePlotControls } from "./SurfacePlotControls";
import styles from "./LayerPanel.module.css";

export interface LayerPanelProps {
  availabilities: ReadonlySet<StationAvailability>;
}

const ORDERABLE = new Set<string>(ORDERABLE_LAYER_IDS);
const descriptor = (id: string): LayerDescriptor | undefined => LAYER_REGISTRY.find((item) => item.id === id);
/** The map layers, in the order the panel lists them. */
const MAP_LAYERS = ["model-domain", "place-labels", "roads", "radar-site-labels", "stations", "station-labels", "basemap"]
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
    case "satellite":
      return <SatelliteControls />;
    case "radar":
      return <RadarControls />;
    case "storm-reports":
      return <ReportControls />;
    case "surface-plots":
      return <SurfacePlotControls />;
    default:
      return <OutlookStatus panel={panel} product={id.slice("spc-".length) as "cat" | "torn" | "wind" | "hail"} />;
  }
}

export function LayerPanel({ availabilities }: LayerPanelProps): ReactNode {
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
  const topFirst = [...order].reverse().filter((id) => ORDERABLE.has(id) && layerInView(id, live ? "live" : "map"));
  const active = topFirst.filter((id) => states[id].visible);
  const available = topFirst.filter((id) => !states[id].visible);

  return (
    <section aria-label={layout === 1 ? "Layers" : `Layers, panel ${String(panel + 1)}`} className={styles.panel} data-testid="layer-panel">
      <div className={styles.context}>
        <div className={styles.contextHeading}>
          <strong>{live ? "Live Tracking" : context === null ? "Weather overlays" : `${context.model.label} ${forecastLabel(context.hour)}`}</strong>
          <span className={styles.dataMode} data-fixture={import.meta.env.MODE === "fixture"} data-testid="layer-data-mode">
            {import.meta.env.MODE === "fixture" ? "Test data · synthetic weather layers" : "Live providers"}
          </span>
        </div>
        {live ? <span className={styles.hint}>Viewing · {formatUtc(validTime)} UTC</span> : context === null ? null : <span className={styles.hint}>Forecast valid · {compactUtc(context.validTime)} UTC</span>}
      </div>
      <div className={styles.categories}>
        {layout === 1 ? null : <div className={styles.panelPicker} role="group" aria-label="Active map panel">
          {Array.from({ length: layout }, (_, index) => <button key={index} type="button" aria-pressed={panel === index}
            onClick={() => { useMapStore.getState().setActivePanel(index); }}>Panel {index + 1}</button>)}
        </div>}
        <div className={styles.groups} role="group" aria-label="Layer categories">
          <button type="button" aria-pressed={group === "weather"} onClick={() => { useMapStore.getState().setLayerPanelGroup("weather"); }}>
            <CloudSun className="icon" aria-hidden="true" /> Weather
          </button>
          <button type="button" aria-pressed={group === "legend"} onClick={() => { useMapStore.getState().setLayerPanelGroup("legend"); }}>
            <List className="icon" aria-hidden="true" /> Legend
          </button>
          <button type="button" aria-label="Map details" aria-pressed={group === "map"} onClick={() => { useMapStore.getState().setLayerPanelGroup("map"); }}>
            <MapIcon className="icon" aria-hidden="true" /> Map
          </button>
        </div>
      </div>
      <div className={styles.body}>
        {group !== "weather" ? null : <div className={styles.weatherLayout}>
          <section className={styles.layerColumn} aria-label="Active weather layers">
          <div className={styles.stackHeading}><h3 className={styles.group}>On map <span>{active.length} · top first</span></h3>
            <IconAction icon={EyeOff} size="sm" label="Hide all weather layers" disabled={active.length === 0}
              onClick={() => { for (const id of active) useMapStore.getState().setLayerVisible(id, false); }} />
          </div>
          <div className={styles.layerList}>
          {active.length === 0 ? <p className={styles.hint}>Choose a layer from Add layers.</p> : null}
          {active.map((id, index) => {
            const layer = descriptor(id);
            return layer === undefined ? null : (
                <LayerRow key={id} layer={layer} state={states[id]} order={{ id, fromTop: index, count: active.length }}>
                  <WeatherExtra id={id} panel={panel} />
                </LayerRow>
            );
          })}
          </div></section>
          <section className={styles.layerColumn} aria-label="Available weather layers">
            <h3 className={styles.group}>Add layers <span>{available.length} available</span></h3>
            <div className={styles.layerList}>{available.map((id) => {
              const layer = descriptor(id);
              return layer === undefined ? null : <LayerRow key={id} layer={layer} state={states[id]} />;
            })}{available.length === 0 ? <p className={styles.hint}>All weather layers are on the map.</p> : null}</div>
          </section>
        </div>}
        {group !== "legend" ? null : <div className={styles.scrollBody}><WeatherLegend active={active} panel={panel} availabilities={availabilities} /></div>}
        {group !== "map" ? null : <div className={styles.scrollBody}>
          <div className={styles.projection}>
            <h3 className={styles.group}>Projection <span>{live ? "Flat map" : "All panels"}</span></h3>
            {live ? null : <div className={styles.panelPicker} role="group" aria-label="Map projection">
              {(["mercator", "globe"] as const).map((value) => <button key={value} type="button" aria-pressed={projection === value}
                onClick={() => { useMapStore.getState().setProjection(value); }}>{value === "globe" ? "Globe" : "Flat map"}</button>)}
            </div>}
          </div>
          <h3 className={styles.group}>Map details</h3>
          <div>{MAP_LAYERS.filter((layer) => layerInView(layer.id, live ? "live" : "map") && (!live || layer.id !== "model-domain")).map((layer) => (
            <LayerRow key={layer.id} layer={layer} state={states[layer.id]}>
              {MAP_EXTRA[layer.id] ?? null}
            </LayerRow>
          ))}{states["place-labels"].visible ? <PlaceStatus /> : null}</div>
        </div>}
      </div>
      <footer className={styles.footer}>
        <span role="status" aria-label="Layer settings status">{reset ? live ? "Live layers reset" : "Layers reset · all panels" : "Saves automatically"}</span>
        <Action title={live ? "Reset live layers and weather options." : "Reset layers, projection and weather options in all panels. Keep the layout, parameters and map location."}
          onClick={() => { resetLayerSettings(); setReset(true); }}>Reset layers</Action>
      </footer>
    </section>
  );
}

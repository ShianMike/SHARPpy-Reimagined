// Enabled weather layers lead in their draw order; the remaining layers can
// be added below. Settings and live provider controls edit the active panel.

import { CloudSun, Map as MapIcon } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { useForecastContext } from "../../features/forecast/useForecast";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { compactUtc, formatUtc, forecastLabel } from "../../features/sounding/soundingTime";
import { mapProjection, panelLayers, useMapStore } from "../../stores/mapStore";
import { resetLayerSettings } from "../../stores/preferences";
import type { StationAvailability } from "../../types/contracts";
import { Action } from "../../ui/Controls";
import { LAYER_REGISTRY } from "../layers/descriptors";
import { layerInView, ORDERABLE_LAYER_IDS, type LayerDescriptor, type OrderableLayerId } from "../layers/registry";
import { WeatherAlertStatus } from "./WeatherAlertStatus";
import { AvailabilityControl } from "./AvailabilityControl";
import { FieldControls } from "./FieldControls";
import { LayerRow } from "./LayerRow";
import { Legend } from "./Legend";
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
const MAP_LAYERS = ["model-domain", "place-labels", "radar-site-labels", "stations", "station-labels", "basemap"]
  .map(descriptor)
  .filter((item): item is LayerDescriptor => item !== undefined && item.userControllable);

/** The map layers' own controls and status, shown while the layer is on. */
const MAP_EXTRA: Readonly<Partial<Record<string, ReactNode>>> = {
  stations: <AvailabilityControl />,
  "place-labels": <PlaceStatus />,
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
          <button type="button" aria-pressed={group === "map"} onClick={() => { useMapStore.getState().setLayerPanelGroup("map"); }}>
            <MapIcon className="icon" aria-hidden="true" /> Map details
          </button>
        </div>
      </div>
      <div className={styles.body}>
        <div data-motion="fade" inert={group !== "weather"} hidden={group !== "weather"}>
          <h3 className={styles.group}>On map <span>{active.length} · top first</span></h3>
          {active.length === 0 ? <p className={styles.hint}>Turn on a weather layer below.</p> : null}
          {[...active, ...available].map((id, index) => {
            const layer = descriptor(id);
            return layer === undefined ? null : (
              <Fragment key={id}>
                {index === active.length ? <h3 className={styles.group}>Add weather layer <span>{available.length} available</span></h3> : null}
                <LayerRow layer={layer} state={states[id]} order={states[id].visible ? { id, fromTop: index, count: active.length } : undefined}>
                  <WeatherExtra id={id} panel={panel} />
                </LayerRow>
              </Fragment>
            );
          })}
        </div>
        <div data-motion="fade" inert={group !== "map"} hidden={group !== "map"}>
          <div className={styles.projection}>
            <h3 className={styles.group}>Map view <span>{live ? "Live Tracking" : "All panels"}</span></h3>
            <div className={styles.panelPicker} role="group" aria-label="Map projection">
              {(["mercator", "globe"] as const).map((value) => <button key={value} type="button" aria-pressed={projection === value} disabled={live && value === "globe"}
                onClick={() => { useMapStore.getState().setProjection(value); }}>{value === "globe" ? "Globe" : "Flat map"}</button>)}
            </div>
            <p className={styles.hint}>{live ? "Live Tracking uses a flat map. Globe is available in Model Viewer." : "Layers and model boundaries adapt to this view."}</p>
          </div>
          <h3 className={styles.group}>Map details</h3>
          {MAP_LAYERS.filter((layer) => layerInView(layer.id, live ? "live" : "map") && (!live || layer.id !== "model-domain")).map((layer) => (
            <LayerRow key={layer.id} layer={layer} state={states[layer.id]}>
              {MAP_EXTRA[layer.id] ?? null}
            </LayerRow>
          ))}
          <Legend availabilities={availabilities} />
        </div>
      </div>
      <footer className={styles.footer}>
        <span role="status">{reset ? live ? "Live layers reset" : "Layers reset · all panels" : "Saves automatically"}</span>
        <Action title={live ? "Reset live layers and weather options." : "Reset layers, projection and weather options in all panels. Keep the layout, parameters and map location."}
          onClick={() => { resetLayerSettings(); setReset(true); }}>Reset layers</Action>
      </footer>
    </section>
  );
}

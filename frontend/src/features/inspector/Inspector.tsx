// Location, readings, and the sounding action lead; provenance stays in a
// disclosure. A removed station still offers a clear-selection recovery.

import { Activity, MapPinOff, RadioTower } from "lucide-react";
import type { ReactNode } from "react";
import { Action } from "../../ui/Controls";
import { Surface } from "../../ui/Surface";
import { IconAction } from "../../ui/Controls";
import { cx } from "../../lib/cx";
import { useAppStore, type MapPoint } from "../../stores/appStore";
import { useMapStore } from "../../stores/mapStore";
import { displayName, formatCoordinates } from "../../stations/stationFormat";
import { useSelectedStation } from "../../stations/stationIndex";
import { useStationCatalog } from "../../stations/stationQueries";
import { useForecastContext } from "../forecast/useForecast";
import { compactUtc, forecastLabel } from "../sounding/soundingTime";
import { openModelSounding, PointDetails } from "./PointDetails";
import { openInSounding, StationDetails } from "./StationDetails";
import styles from "./Inspector.module.css";

export interface InspectorProps {
  className?: string | undefined;
}

function clearSelection(): void {
  useAppStore.getState().clearSelection();
}

function PointInspector({ point, className }: { point: MapPoint; className?: string | undefined }): ReactNode {
  const context = useForecastContext();
  return (
    <Surface
      as="aside"
      aria-label="Inspector"
      elevation="floating"
      className={cx(styles.inspector, className)}
      data-testid="inspector"
      data-kind="point"
    >
      <div className={styles.header}>
        <div className={styles.heading}>
          <p className={styles.id}>Forecast point</p>
          <p className={cx(styles.coordinates, "tabular")}>{formatCoordinates(point)}</p>
        </div>
        <IconAction icon={MapPinOff} size="sm" label="Clear selection" onClick={clearSelection} />
      </div>
      {context === null ? null : <div className={styles.context} data-testid="point-forecast">
        <strong>{context.model.label} <span className="tabular">{forecastLabel(context.hour)}</span></strong>
        <span className="tabular">Valid {compactUtc(context.validTime)} UTC</span>
      </div>}
      <div className={styles.body}>
        <PointDetails point={point} />
      </div>
      <div className={styles.footer}>
        <Action
          tone="accent"
          disabled={context === null}
          onClick={() => {
            if (context !== null) openModelSounding(context, point);
          }}
          data-testid="open-model-here"
          aria-label={context === null ? "Open model sounding here" : `Open ${context.model.label} sounding here`}
        >
          <Activity className="icon" aria-hidden="true" /> Open sounding
        </Action>
      </div>
    </Surface>
  );
}

export function Inspector({ className }: InspectorProps): ReactNode {
  const selection = useSelectedStation();
  const point = useAppStore((state) => state.selectedPoint);
  const catalog = useStationCatalog();
  if (point !== null) {
    return <PointInspector point={point} className={className} />;
  }
  if (selection.state === "none" || selection.state === "waiting" || catalog.data === undefined) {
    return null;
  }
  const found = selection.state === "found";
  return (
    <Surface
      as="aside"
      aria-label="Inspector"
      elevation="floating"
      className={cx(styles.inspector, className)}
      data-testid="inspector"
      data-kind="station"
    >
      <div className={styles.header}>
        <span className={styles.locationIcon}><RadioTower className="icon" aria-hidden="true" /></span>
        <div className={styles.heading}>
          <p className={styles.caption}>Station</p>
          <p className={cx(styles.id, "tabular")}>{selection.id}</p>
          {found ? <p className={styles.name}>{displayName(selection.station.name)}</p> : null}
        </div>
        <IconAction icon={MapPinOff} size="sm" label="Clear selection" onClick={clearSelection} />
      </div>
      <div className={styles.body}>
        {found ? (
          <StationDetails
            station={selection.station}
            origin={catalog.data.origin}
            stationCount={catalog.data.stations.length}
          />
        ) : (
          <>
            <p className={styles.missing}>{`Station ${selection.id} is not in the current catalogue`}</p>
            <Action onClick={clearSelection}>Clear selection</Action>
          </>
        )}
      </div>
      {found ? (
        <div className={styles.footer}>
          <Action onClick={() => { useMapStore.getState().requestFocus(selection.station.id); }}>
            Zoom to station
          </Action>
          <Action tone="accent" onClick={() => { openInSounding(selection.station.id); }}>
            Open sounding
          </Action>
        </div>
      ) : null}
    </Surface>
  );
}

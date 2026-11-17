// Location, readings, and the sounding action lead; provenance stays in a
// disclosure. A removed station still offers a clear-selection recovery.

import { Activity, Check, Copy, MapPin, MapPinOff, RadioTower } from "lucide-react";
import { useState, type ReactNode } from "react";
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
import { SurfacePlotControls } from "../../map/controls/SurfacePlotControls";
import styles from "./Inspector.module.css";

export interface InspectorProps {
  className?: string | undefined;
}

function clearSelection(): void {
  useAppStore.getState().clearSelection();
}

function PointInspector({ point, className }: { point: MapPoint; className?: string | undefined }): ReactNode {
  const context = useForecastContext();
  const live = useMapStore((state) => state.view === "live");
  const coordinates = `${point.latitude.toFixed(4)}, ${point.longitude.toFixed(4)}`;
  const [copy, setCopy] = useState<{ coordinates: string; state: "copying" | "copied" | "error" } | null>(null);
  const copyState = copy?.coordinates === coordinates ? copy.state : null;
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
        <span className={styles.locationIcon}><MapPin className="icon" aria-hidden="true" /></span>
        <div className={styles.heading}>
          <p className={styles.id}>{live ? "Live location" : "Forecast point"}</p>
          <p className={cx(styles.coordinates, "tabular")}>{formatCoordinates(point)}</p>
        </div>
        <div className={styles.locationActions}>
          <IconAction icon={copyState === "copied" ? Check : Copy} size="sm" label={copyState === "copied" ? "Coordinates copied" : "Copy coordinates"} loading={copyState === "copying"} disabled={copy?.state === "copying"}
            onClick={() => {
              if (!("clipboard" in navigator)) { setCopy({ coordinates, state: "error" }); return; }
              setCopy({ coordinates, state: "copying" });
              void navigator.clipboard.writeText(coordinates).then(
                () => { setCopy({ coordinates, state: "copied" }); },
                () => { setCopy({ coordinates, state: "error" }); },
              );
            }} />
          <IconAction icon={MapPinOff} size="sm" label="Clear selection" onClick={clearSelection} />
        </div>
      </div>
      {copyState === "copied" || copyState === "error" ? <p className={styles.copyNotice} role={copyState === "error" ? "alert" : "status"} data-error={copyState === "error"}>
        {copyState === "copied" ? "Coordinates copied." : "Couldn’t copy. Select the coordinates to copy manually."}
      </p> : null}
      {live || context === null ? null : <div className={styles.context} data-testid="point-forecast">
        <strong>{context.model.label} <span className="tabular">{forecastLabel(context.hour)}</span></strong>
        <time className="tabular" dateTime={context.validTime}>Valid {compactUtc(context.validTime)} UTC</time>
      </div>}
      <div className={styles.body}>
        {live ? <SurfacePlotControls /> : <PointDetails point={point} />}
      </div>
      {live || context?.model.soundingsAvailable === false ? null : <div className={styles.footer}>
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
      </div>}
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

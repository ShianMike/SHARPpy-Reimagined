import type { CSSProperties, ReactNode } from "react";
import { RadarScale } from "../../features/tracking/RadarScale";
import { useWeatherTime } from "../../features/tracking/useWeatherTime";
import { formatUtc } from "../../features/sounding/soundingTime";
import { panelLayers, panelWeather, useMapStore } from "../../stores/mapStore";
import type { StationAvailability } from "../../types/contracts";
import { OUTLOOK_KEYS, type OutlookKey } from "../../types/map";
import { LAYER_REGISTRY } from "../layers/descriptors";
import type { OrderableLayerId } from "../layers/registry";
import { SYMBOL_PATHS } from "../layers/StormReportLayer";
import { useSatelliteFrame, useSpcOutlooks, useStormReports } from "../mapQueries";
import { usePanelField } from "../panelField";
import { useWeatherAlerts } from "../weatherAlertsQuery";
import { FieldLegend } from "./FieldLegend";
import { Legend } from "./Legend";
import { SurfacePlotControls } from "./SurfacePlotControls";
import styles from "./Legend.module.css";

function ColourKeys({ entries }: { entries: readonly { label: string; fill: string | null; stroke: string; hatch?: boolean }[] }) {
  const unique = [...new Map(entries.map((entry) => [JSON.stringify(entry), entry])).values()];
  return <ul className={styles.keys}>
    {unique.map((entry) => <li key={JSON.stringify(entry)}>
      <span className={styles.colourSwatch} data-hatched={entry.hatch} aria-hidden="true"
        style={{ "--key-fill": entry.fill ?? "transparent", "--key-stroke": entry.stroke } as CSSProperties} />
      <span>{entry.label}{entry.hatch ? " · hatched" : ""}</span>
    </li>)}
  </ul>;
}

function ModelKey({ panel }: { panel: number }) {
  const field = usePanelField(panel);
  return field.product === null ? <p>Select a model parameter to see its color key.</p> : <>
    <strong>{field.product.label}</strong><FieldLegend palette={field.product.palette} />
    {field.product.contour === null ? null : <p>Contours · {field.product.contour.label}</p>}
    {field.frame.isError ? <p>Field unavailable. Open its settings to retry.</p> : null}
  </>;
}

function AlertKey({ kind }: { kind: "warning" | "watch" }) {
  const time = useWeatherTime();
  const query = useWeatherAlerts(time, true);
  const alerts = query.data?.alerts.filter((alert) => alert.kind === kind) ?? [];
  const plural = kind === "watch" ? "watches" : "warnings";
  return time === null ? <p>Choose a time to view NWS alerts.</p> : query.isError ? <p>Alerts unavailable. Open layer settings to retry.</p>
    : query.data === undefined ? <p>Loading NWS alerts…</p> : <>
      <ColourKeys entries={alerts.map((alert) => ({ label: alert.event, fill: null, stroke: alert.colour }))} />
      <p>{alerts.length === 0 ? `No active ${plural} at this time.` : `${String(alerts.length)} active ${alerts.length === 1 ? kind : plural}`}</p>
    </>;
}

function OutlookKey({ product }: { product: OutlookKey }) {
  const time = useWeatherTime();
  const query = useSpcOutlooks(OUTLOOK_KEYS.map((key) => key === product), time)[OUTLOOK_KEYS.indexOf(product)];
  const outlook = query?.data?.outlook;
  return time === null ? <p>Choose a forecast time to view SPC outlooks.</p> : query?.isError === true ? <p>Outlook unavailable. Open layer settings to retry.</p>
    : query?.data === undefined ? <p>Loading SPC outlook…</p> : outlook?.covers !== true ? <p>No SPC outlook covers this time.</p> : <>
      <ColourKeys entries={outlook.areas.map(({ label, fill, stroke, hatch }) => ({ label, fill, stroke, hatch }))} />
      <p>{outlook.subtitle}</p>
    </>;
}

function ReportKey() {
  const time = useWeatherTime();
  const windowS = useMapStore((state) => state.reportWindowS);
  const filter = useMapStore((state) => state.reportFilter);
  const query = useStormReports(time, windowS, filter, true);
  const symbols = [...new Map(query.data?.reports.map((report) => [JSON.stringify([report.category, report.symbol, report.fill, report.stroke]), report])).values()];
  return time === null ? <p>Choose a forecast time to view reports.</p> : query.isError ? <p>Reports unavailable. Open layer settings to retry.</p>
    : query.data === undefined ? <p>Loading report symbols…</p> : <>
      <ul className={styles.keys}>{symbols.map((report) => <li key={JSON.stringify([report.category, report.symbol, report.fill, report.stroke])}>
        <svg className={styles.symbol} viewBox="0 0 24 24" aria-hidden="true"><path d={SYMBOL_PATHS[report.symbol] ?? SYMBOL_PATHS.circle} fill={report.fill ?? "none"} stroke={report.stroke} /></svg>
        {report.category}
      </li>)}</ul>
      <p>{query.data.shownCount === 0 ? "No matching reports." : `${String(query.data.shownCount)} reports · ${query.data.windowLabel}`}</p>
    </>;
}

function SatelliteKey({ panel }: { panel: number }) {
  const time = useWeatherTime();
  const channel = useMapStore((state) => panelWeather(state, panel).satelliteChannel);
  const query = useSatelliteFrame(channel, time, true);
  return time === null ? <p>Choose a time to view GOES imagery.</p> : query.isError ? <p>Imagery unavailable. Open layer settings to retry.</p>
    : query.data === undefined ? <p>Loading GOES imagery…</p> : <>
      <strong>{query.data.label}</strong>
      <p>{channel === "infrared" ? "Colors show cloud-top temperature." : "Brightness shows reflected sunlight."} Uncovered areas are transparent.</p>
      <p>Scan {formatUtc(query.data.validTime)} UTC</p>
    </>;
}

function WeatherKey({ id, panel }: { id: OrderableLayerId; panel: number }): ReactNode {
  switch (id) {
    case "model-field": return <ModelKey panel={panel} />;
    case "radar": return <RadarScale panel={panel} horizontal />;
    case "satellite": return <SatelliteKey panel={panel} />;
    case "warnings": return <AlertKey kind="warning" />;
    case "watches": return <AlertKey kind="watch" />;
    case "storm-reports": return <ReportKey />;
    case "surface-plots": return <SurfacePlotControls />;
    default: return <OutlookKey product={id.slice("spc-".length) as OutlookKey} />;
  }
}

export function WeatherLegend({ active, panel, availabilities }: { active: readonly OrderableLayerId[]; panel: number; availabilities: ReadonlySet<StationAvailability> }) {
  const layers = useMapStore((state) => panelLayers(state, panel));
  return <section aria-label="Map legend" className={styles.weather}>
    <h3>Enabled layers <span>{active.length} · top first</span></h3>
    {active.length === 0 ? <p>No weather layers enabled. Add one in Weather to see its key.</p> : <ol className={styles.stack}>
      {active.map((id, index) => <li key={id} className={styles.card} data-testid={`legend-${id}`}>
        <h4><span>{index + 1}</span><span>{LAYER_REGISTRY.find((layer) => layer.id === id)?.label}
          <small title="Color keys show source colors before map opacity is applied.">{Math.round(layers[id].opacity * 100)}% opacity</small></span></h4>
        <div className={styles.keyBody}>{layers[id].opacity === 0 ? <p>Fully transparent on the map.</p> : null}<WeatherKey id={id} panel={panel} /></div>
      </li>)}
    </ol>}
    {!layers.stations.visible ? null : <div className={styles.stationKey}><h3>Stations</h3><Legend availabilities={availabilities} /></div>}
  </section>;
}

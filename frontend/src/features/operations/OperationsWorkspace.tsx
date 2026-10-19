import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useMapCatalog } from "../../map/mapQueries";
import { productOf } from "../../map/panelField";
import { useStationCatalog } from "../../stations/stationQueries";
import { useAppStore, type WorkspaceId } from "../../stores/appStore";
import { useLogStore } from "../../stores/logStore";
import { panelProduct, useMapStore } from "../../stores/mapStore";
import { useSoundingStore, type SoundingSource } from "../../stores/soundingStore";
import { Action } from "../../ui/Controls";
import { BrandMark } from "../../ui/BrandMark";
import { useForecast } from "../forecast/useForecast";
import { compactUtc, forecastLabel, formatUtc } from "../sounding/soundingTime";
import { HealthIndicator } from "../toolbar/HealthIndicator";
import styles from "./OperationsWorkspace.module.css";
import { ModelBackendStatus } from "./ModelBackendStatus";

const SOURCES: readonly { source: SoundingSource; label: string; hint: string }[] = [
  { source: "observed", label: "Observed", hint: "Weather balloon profiles" },
  { source: "model", label: "Forecast", hint: "Model profiles at a point" },
  { source: "era5", label: "ERA5 archive", hint: "Past atmospheric conditions" },
  { source: "file", label: "Open a file", hint: "A profile from your computer" },
];

export function OperationsWorkspace(): ReactNode {
  const [providersOpen, setProvidersOpen] = useState(false);
  const forecast = useForecast();
  const context = forecast.status === "ready" ? forecast.context : null;
  const catalog = useMapCatalog();
  const stations = useStationCatalog();
  const panel = useMapStore((state) => state.activePanel);
  const chosen = useMapStore((state) => panelProduct(state, panel));
  const product = productOf(catalog.data, chosen, panel);
  const entries = useLogStore((state) => state.entries);
  const errors = entries.filter((entry) => entry.level === "error").length;
  const navigate = (id: WorkspaceId): void => { useAppStore.getState().setActiveWorkspace(id); };
  return <div className={styles.page}>
    <header className={styles.heading}><div className={styles.identity}><BrandMark className={styles.brandMark} /><div><h1>SHARPpy Reimagined</h1><p>Your weather workspace. Choose where to begin.</p></div></div><HealthIndicator /></header>
    <div className={styles.workflows}>
      <section className={styles.workflow} aria-labelledby="continue-title">
        <header className={styles.workflowHeading}><h2 id="continue-title">Model Viewer</h2><p>Explore forecast guidance.</p></header>
        {context === null ? <div className={styles.forecastCopy}><h3>{forecast.status === "loading" ? "Preparing your forecast…" : "Forecast unavailable"}</h3><p>{forecast.status === "error" ? forecast.error.message : forecast.status === "empty" ? "No forecast models are available from the backend." : "Loading models and parameters."}</p></div> : <>
          <div className={styles.forecastCopy}><h3>{context.model.label} <span>{forecastLabel(context.hour)}</span></h3><p>{context.fieldsAvailable ? product?.label ?? "Model guidance" : "Point soundings available for this model"}</p></div>
          <dl className={styles.facts}>
            <div><dt>Valid · UTC</dt><dd><time dateTime={context.validTime} title={formatUtc(context.validTime)}>{compactUtc(context.validTime)}</time></dd></div>
            <div><dt>Run · UTC</dt><dd><time dateTime={context.run} title={formatUtc(context.run)}>{compactUtc(context.run)}</time></dd></div>
            <div><dt>Forecast range</dt><dd>{forecastLabel(context.hours[0] ?? 0)} – {forecastLabel(context.hours.at(-1) ?? 0)}</dd></div>
          </dl>
        </>}
        <div className={styles.workflowFooter}>{forecast.status === "error" ? <Action disabled={forecast.retrying} onClick={forecast.retry}>Retry catalogue</Action> : null}<Action tone="accent" onClick={() => { navigate("map"); }}>Open Model Viewer</Action></div>
      </section>
      <section className={styles.workflow} aria-labelledby="live-start-title">
        <header className={styles.workflowHeading}><h2 id="live-start-title">Live Tracking</h2><p>Live layers. Past-hour playback.</p></header>
        <ul className={styles.liveFeatures}>
          <li><strong>Radar</strong><p>Base Reflectivity, Velocity, Correlation Coefficient, and Enhanced Echo Tops.</p></li>
          <li><strong>Satellite & observations</strong><p>Cloud imagery and conditions on the ground.</p></li>
          <li><strong>Warnings & watches</strong><p>Active alerts alongside your live layers.</p></li>
        </ul>
        <div className={styles.workflowFooter}><Action tone="accent" onClick={() => { navigate("live"); }}>Open Live Tracking</Action></div>
      </section>
      <section className={styles.workflow} aria-labelledby="sounding-start-title">
        <header className={styles.workflowHeading}><h2 id="sounding-start-title">Soundings</h2><p>Explore the atmosphere in detail.</p></header>
        <div className={styles.sources}>{SOURCES.map(({ source, label, hint }) => <Action key={source} className={styles.source} aria-label={label} aria-describedby={`overview-source-${source}`} onClick={() => { useSoundingStore.getState().setSource(source); navigate("sounding"); }}><strong>{label}</strong><small id={`overview-source-${source}`}>{hint}</small></Action>)}</div>
      </section>
    </div>
    <div className={styles.diagnostics}>
      {context === null ? null : <details className={styles.disclosure} onToggle={(event) => { setProvidersOpen(event.currentTarget.open); }}>
        <summary><span>Model availability</span><small>{String(context.models.length)} models</small><ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        {providersOpen ? <ModelBackendStatus models={context.models} /> : null}
      </details>}
      <details className={styles.disclosure}>
        <summary><span>Recent activity</span><small className={styles.activityStatus} data-error={errors > 0}>{errors > 0 ? `${String(errors)} ${errors === 1 ? "error" : "errors"} this session` : "No errors this session"}</small><ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        <div className={styles.activity}>
          <div className={styles.sectionHeading}><p>Latest requests and connection changes</p><Action onClick={() => { navigate("status"); }}>View logs</Action></div>
          {entries.length === 0 ? <p className={styles.empty}>No activity recorded yet.</p> : <ul className={styles.events}>{entries.slice(0, 3).map((entry) => <li key={entry.id} data-level={entry.level}><span className={styles.eventDot} aria-hidden="true" /><span><strong>{entry.source.replaceAll("_", " ")}</strong><small>{entry.message}</small></span><time dateTime={entry.time}>{entry.time.slice(11, 16)} UTC</time></li>)}</ul>}
        </div>
      </details>
    </div>
    <footer className={styles.footer}>
      <div className={styles.preferences}><p>Your layers, panels, and preferences are saved on this device.</p><Action onClick={() => { navigate("settings"); }}>Open Settings</Action></div>
      <div className={styles.catalogue}><span>{forecast.status === "loading" ? "Models loading…" : context === null ? "Model catalogue unavailable" : `${String(context.models.length)} forecast models`}</span><span>{catalog.isError ? "Parameter catalogue unavailable" : catalog.data === undefined ? "Parameters loading…" : `${String(catalog.data.products.length)} map parameters`}</span><span>{stations.isError ? "Station catalogue unavailable" : stations.data === undefined ? "Stations loading…" : `${String(stations.data.stations.length)} stations`}</span><span>Times in UTC</span></div>
    </footer>
  </div>;
}

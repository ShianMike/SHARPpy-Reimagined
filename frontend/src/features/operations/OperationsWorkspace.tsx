import { ArrowUpRight, ChevronDown, FileUp, History, Layers, Map, RadioTower, ScanLine, Star } from "lucide-react";
import { useState, type ReactNode } from "react";
import { version } from "../../../package.json";
import { cx } from "../../lib/cx";
import { useMapCatalog } from "../../map/mapQueries";
import { productOf } from "../../map/panelField";
import { useStationCatalog } from "../../stations/stationQueries";
import { useAppStore, type WorkspaceId } from "../../stores/appStore";
import { useLogStore } from "../../stores/logStore";
import { panelProduct, useMapStore } from "../../stores/mapStore";
import { useSoundingStore, type SoundingSource } from "../../stores/soundingStore";
import { Action } from "../../ui/Controls";
import actionStyles from "../../ui/Controls.module.css";
import { BrandMark } from "../../ui/BrandMark";
import { useForecast } from "../forecast/useForecast";
import { compactUtc, forecastLabel, formatUtc } from "../sounding/soundingTime";
import { HealthIndicator } from "../toolbar/HealthIndicator";
import styles from "./OperationsWorkspace.module.css";
import { ModelBackendStatus } from "./ModelBackendStatus";
import { SoundingErrorNotice } from "../sounding/SoundingErrorNotice";
import { SpcBriefing } from "./SpcBriefing";

const SOURCES = [
  { source: "observed", label: "Observed", hint: "Weather balloon profiles", icon: RadioTower },
  { source: "model", label: "Forecast", hint: "Model profiles at a point", icon: Layers },
  { source: "era5", label: "ERA5 archive", hint: "Past atmospheric conditions", icon: History },
  { source: "file", label: "Open a file", hint: "A profile from your computer", icon: FileUp },
] as const satisfies readonly { source: SoundingSource; label: string; hint: string; icon: typeof RadioTower }[];

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
  const openSoundings = useSoundingStore((state) => state.open.length);
  const errors = entries.filter((entry) => entry.level === "error").length;
  const navigate = (id: WorkspaceId): void => { useAppStore.getState().setActiveWorkspace(id); };
  return <div className={styles.page}>
    <header className={styles.heading}><div className={styles.identity}><BrandMark className={styles.brandMark} /><div><div className={styles.title}><h1>SHARPpy Reimagined</h1><span className={styles.version} title="Current app version">v{version}</span></div><p>Your weather briefing. Current outlooks, your next analysis.</p></div></div><HealthIndicator /></header>
    <SpcBriefing />
    <div className={styles.workflows}>
      <section className={styles.workflow} aria-labelledby="continue-title">
        <header className={styles.workflowHeading}><Map className="icon" aria-hidden="true" /><h2 id="continue-title">Model Viewer</h2><span>Panel {String(panel + 1)}</span></header>
        {context === null ? <div className={styles.forecastCopy}>{forecast.status === "error"
          ? <SoundingErrorNotice error={forecast.error} operation="get_model_catalog" onRetry={forecast.retry} busy={forecast.retrying} testId="overview-model-error" />
          : <><h3>{forecast.status === "loading" ? "Preparing your forecast…" : "Forecast unavailable"}</h3><p>{forecast.status === "empty" ? "No forecast models are available." : "Loading models and parameters."}</p></>}</div> : <>
          <div className={styles.forecastCopy}><h3>{context.model.label} <span>{forecastLabel(context.hour)}</span></h3><p>{context.fieldsAvailable ? product?.label ?? "Model guidance" : context.model.soundingsAvailable ? "Point soundings available for this model" : "Maps only"}</p></div>
          <dl className={styles.facts}>
            <div><dt>Valid · UTC</dt><dd><time dateTime={context.validTime} title={formatUtc(context.validTime)}>{compactUtc(context.validTime)}</time></dd></div>
            <div><dt>Run · UTC</dt><dd><time dateTime={context.run} title={formatUtc(context.run)}>{compactUtc(context.run)}</time></dd></div>
          </dl>
        </>}
        <div className={styles.workflowFooter}><Action tone="accent" onClick={() => { navigate("map"); }}>Open Model Viewer <ArrowUpRight className="icon-sm" aria-hidden="true" /></Action></div>
      </section>
      <section className={styles.workflow} aria-labelledby="live-start-title">
        <header className={styles.workflowHeading}><ScanLine className="icon" aria-hidden="true" /><h2 id="live-start-title">Live Tracking</h2></header>
        <ul className={styles.liveFeatures}>
          <li><strong>Follow the weather as it unfolds.</strong><p>Radar, satellite, surface observations, warnings and storm reports.</p></li>
          <li><p>Inspect Correlation Coefficient and Enhanced Echo Tops, or replay the past hour.</p></li>
        </ul>
        <div className={styles.workflowFooter}><Action onClick={() => { navigate("live"); }}>Open Live Tracking <ArrowUpRight className="icon-sm" aria-hidden="true" /></Action></div>
      </section>
      <section className={styles.workflow} aria-labelledby="sounding-start-title">
        <header className={styles.workflowHeading}><RadioTower className="icon" aria-hidden="true" /><h2 id="sounding-start-title">Soundings</h2>{openSoundings > 0 ? <span>{String(openSoundings)} open</span> : null}</header>
        <div className={styles.sources}>{SOURCES.map(({ source, label, hint, icon: Icon }) => <Action key={source} className={styles.source} aria-label={label} title={hint} onClick={() => { useSoundingStore.getState().setSource(source); navigate("sounding"); }}><Icon className="icon-sm" aria-hidden="true" /><strong>{label}</strong><ArrowUpRight className="icon-sm" aria-hidden="true" /></Action>)}</div>
        {openSoundings > 0 ? <div className={styles.workflowFooter}><Action onClick={() => { navigate("sounding"); }}>Resume sounding <ArrowUpRight className="icon-sm" aria-hidden="true" /></Action></div> : <p className={styles.soundingHint}>Severe & Winter · Inspect, edit and export</p>}
      </section>
    </div>
    <div className={styles.diagnostics}>
      {context === null ? null : <details className={styles.disclosure} onToggle={(event) => { setProvidersOpen(event.currentTarget.open); }}>
        <summary><span>Model availability</span><small>{String(context.models.length)} models</small><ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        {providersOpen ? <ModelBackendStatus models={context.models} /> : null}
      </details>}
      <details className={styles.disclosure} data-motion="details">
        <summary><span>Recent activity</span><small className={styles.activityStatus} data-error={errors > 0}>{errors > 0 ? `${String(errors)} ${errors === 1 ? "error" : "errors"} this session` : "No errors this session"}</small><ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        <div className={styles.activity}>
          <div className={styles.sectionHeading}><p>Latest requests and connection changes</p><Action onClick={() => { navigate("status"); }}>View logs</Action></div>
          {entries.length === 0 ? <p className={styles.empty}>No activity recorded yet.</p> : <ul className={styles.events}>{entries.slice(0, 3).map((entry) => <li key={entry.id} data-level={entry.level}><span className={styles.eventDot} aria-hidden="true" /><span><strong>{entry.source.replaceAll("_", " ")}</strong><small>{entry.message}</small></span><time dateTime={entry.time}>{entry.time.slice(11, 16)} UTC</time></li>)}</ul>}
        </div>
      </details>
    </div>
    <footer className={styles.footer}>
      <div className={styles.repoPrompt}>
        <p>Support SHARPpy Reimagined with a star on GitHub.</p>
        <a className={cx(actionStyles.action, styles.repoLink)} href="https://github.com/ShianMike/SHARPpy-Reimagined" target="_blank" rel="noopener noreferrer">
          <Star className="icon-sm" aria-hidden="true" /> Star the Repo
        </a>
      </div>
      <div className={styles.catalogue}><span>{forecast.status === "loading" ? "Models loading…" : context === null ? "Model catalogue unavailable" : `${String(context.models.length)} forecast models`}</span><span>{catalog.isError ? "Parameter catalogue unavailable" : catalog.data === undefined ? "Parameters loading…" : `${String(catalog.data.products.length)} map parameters`}</span><span>{stations.isError ? "Station catalogue unavailable" : stations.data === undefined ? "Stations loading…" : `${String(stations.data.stations.length)} stations`}</span><span>Times in UTC</span></div>
    </footer>
  </div>;
}

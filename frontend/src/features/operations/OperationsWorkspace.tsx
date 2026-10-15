import { Activity, ArrowRight, FileText, Globe2, History, Settings2, SquareTerminal, Wind } from "lucide-react";
import type { ReactNode } from "react";
import { useMapCatalog } from "../../map/mapQueries";
import { productOf } from "../../map/panelField";
import { useStationCatalog } from "../../stations/stationQueries";
import { useAppStore, type WorkspaceId } from "../../stores/appStore";
import { useLogStore } from "../../stores/logStore";
import { panelProduct, useMapStore } from "../../stores/mapStore";
import { useSoundingStore, type SoundingSource } from "../../stores/soundingStore";
import { Action } from "../../ui/Controls";
import { useForecast } from "../forecast/useForecast";
import { compactUtc, forecastLabel, formatUtc } from "../sounding/soundingTime";
import { HealthIndicator } from "../toolbar/HealthIndicator";
import styles from "./OperationsWorkspace.module.css";
import { ModelBackendStatus } from "./ModelBackendStatus";

const SOURCES: readonly { source: SoundingSource; label: string; hint: string; icon: typeof Activity }[] = [
  { source: "observed", label: "Observed", hint: "Weather balloon profiles", icon: Activity },
  { source: "model", label: "Forecast", hint: "A profile at any forecast point", icon: Wind },
  { source: "era5", label: "ERA5 archive", hint: "Explore past atmospheric conditions", icon: History },
  { source: "file", label: "Open a file", hint: "Load a sounding from your computer", icon: FileText },
];

export function OperationsWorkspace(): ReactNode {
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
    <header className={styles.heading}><div><h1>Overview</h1><p>Your forecast, ready to explore.</p></div><HealthIndicator /></header>
    <div className={styles.workflows}>
      <section className={styles.forecast} aria-labelledby="continue-title">
        <div className={styles.sectionHeading}><Globe2 className="icon" aria-hidden="true" /><h2 id="continue-title">On your map</h2></div>
        {context === null ? <div className={styles.forecastCopy}><h3>{forecast.status === "loading" ? "Preparing your forecast…" : "Forecast unavailable"}</h3><p>{forecast.status === "error" ? forecast.error.message : forecast.status === "empty" ? "No forecast models are available from the backend." : "Loading models and parameters."}</p></div> : <>
          <div className={styles.forecastHero}><div className={styles.forecastCopy}><h3>{context.model.label}<span>{forecastLabel(context.hour)}</span></h3><p>{context.fieldsAvailable ? product?.label ?? "Model guidance" : "Point soundings available for this model"}</p></div><div className={styles.validTime}><span>Valid · UTC</span><time dateTime={context.validTime} title={formatUtc(context.validTime)}>{compactUtc(context.validTime)}</time></div></div>
          <dl className={styles.facts}><div><dt>Model run · UTC</dt><dd title={formatUtc(context.run)}>{compactUtc(context.run)}</dd></div><div><dt>Forecast range</dt><dd>{forecastLabel(context.hours[0] ?? 0)} – {forecastLabel(context.hours.at(-1) ?? 0)}</dd></div></dl>
        </>}
        <div className={styles.forecastFooter}><p>Continue from your current view.</p>{forecast.status === "error" ? <Action disabled={forecast.retrying} onClick={forecast.retry}>Retry catalogue</Action> : null}<Action tone="accent" onClick={() => { navigate("map"); }}>Open map <ArrowRight className="icon-sm" aria-hidden="true" /></Action></div>
      </section>
      <section className={styles.soundings} aria-labelledby="sounding-start-title">
        <div className={styles.sectionHeading}><span className={styles.iconTile}><Activity aria-hidden="true" /></span><div><h2 id="sounding-start-title">Open a sounding</h2><p>Choose where your profile comes from.</p></div></div>
        <div className={styles.sources}>{SOURCES.map(({ source, label, hint, icon: Icon }) => <button key={source} type="button" onClick={() => { useSoundingStore.getState().setSource(source); navigate("sounding"); }}><Icon className="icon" aria-hidden="true" /><span><strong>{label}</strong><small>{hint}</small></span><ArrowRight className="icon-sm" aria-hidden="true" /></button>)}</div>
      </section>
    </div>
    {context === null ? null : <ModelBackendStatus models={context.models} />}
    <div className={styles.secondary}>
      <section className={styles.activity} aria-labelledby="activity-title"><div className={styles.sectionHeading}><SquareTerminal className="icon" aria-hidden="true" /><h2 id="activity-title">Recent activity</h2><Action onClick={() => { navigate("status"); }}>View logs <ArrowRight className="icon-sm" aria-hidden="true" /></Action></div>
        <p className={styles.activityStatus} data-error={errors > 0}>{errors > 0 ? `${String(errors)} ${errors === 1 ? "error" : "errors"} recorded this session` : "No errors recorded this session"}</p>
        {entries.length === 0 ? <p className={styles.empty}>Requests and connection changes will appear here.</p> : <ul className={styles.events}>{entries.slice(0, 3).map((entry) => <li key={entry.id} data-level={entry.level}><span className={styles.eventDot} /><span><strong>{entry.source.replaceAll("_", " ")}</strong><small>{entry.message}</small></span><time dateTime={entry.time}>{entry.time.slice(11, 16)} UTC</time></li>)}</ul>}
      </section>
      <section className={styles.preferences}><span className={styles.iconTile}><Settings2 aria-hidden="true" /></span><h2>A workspace that suits you</h2><p>Adjust appearance, forecast playback, and map rendering. Preferences are saved on this device.</p><Action onClick={() => { navigate("settings"); }}>Personalize workspace <ArrowRight className="icon-sm" aria-hidden="true" /></Action></section>
    </div>
    <footer className={styles.catalogue}><span>{context === null ? "Model catalogue unavailable" : `${String(context.models.length)} forecast models`}</span><span>{catalog.isError ? "Parameter catalogue unavailable" : catalog.data === undefined ? "Parameters loading…" : `${String(catalog.data.products.length)} map parameters`}</span><span>{stations.isError ? "Station catalogue unavailable" : stations.data === undefined ? "Stations loading…" : `${String(stations.data.stations.length)} stations`}</span><span>All forecast times in UTC</span></footer>
  </div>;
}

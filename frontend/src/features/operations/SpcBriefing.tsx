import { ExternalLink, MapPin, RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useCurrentSpcOutlook } from "../../map/mapQueries";
import { containsPoint, type LonLat } from "../../map/layers/outlookGeometry";
import { useStationCatalog } from "../../stations/stationQueries";
import { useAppStore } from "../../stores/appStore";
import { OUTLOOK_KEYS, type OutlookKey } from "../../types/map";
import { IconAction } from "../../ui/Controls";
import { SpcBriefingMap } from "./SpcBriefingMap";
import styles from "./SpcBriefing.module.css";

const PRODUCTS: Record<OutlookKey, string> = { cat: "Categorical", torn: "Tornado", wind: "Wind", hail: "Hail" };
const utc = new Intl.DateTimeFormat("en-GB", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
const EMPTY_AREAS = [] as const;

export function SpcBriefing(): ReactNode {
  const [product, setProduct] = useState<OutlookKey>("cat");
  const [now, setNow] = useState(Date.now);
  const query = useCurrentSpcOutlook(product);
  const point = useAppStore((state) => state.selectedPoint);
  const stationId = useAppStore((state) => state.selectedStationId);
  const stations = useStationCatalog();
  const station = stations.data?.stations.find((item) => item.id === stationId);
  const coordinate: LonLat | null = point !== null ? [point.longitude, point.latitude] : station === undefined ? null : [station.longitude, station.latitude];
  const locationName = point !== null ? `${point.latitude.toFixed(2)}°, ${point.longitude.toFixed(2)}°` : station?.name ?? "";
  useEffect(() => {
    const update = (): void => { setNow(Date.now()); };
    const timer = window.setInterval(update, 30_000);
    window.addEventListener("focus", update);
    document.addEventListener("visibilitychange", update);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", update); document.removeEventListener("visibilitychange", update); };
  }, []);
  const outlook = query.data?.outlook;
  const outlookDay = outlook?.title.match(/\bDay [1-3]\b/)?.[0] ?? "Current period";
  const current = outlook !== undefined && outlook !== null && outlook.covers && outlook.validFrom !== null && outlook.validTo !== null
    && Date.parse(outlook.validFrom) <= now && now < Date.parse(outlook.validTo) && (outlook.issued === null || Date.parse(outlook.issued) <= now);
  const areas = current ? outlook.areas : EMPTY_AREAS;
  const ranked = useMemo(() => [...areas].filter((area) => !area.hatch).sort((a, b) => b.rank - a.rank), [areas]);
  const highest = ranked[0];
  const atPoint = coordinate === null ? undefined : ranked.find((area) => containsPoint(area.rings, coordinate));
  const qualifier = coordinate === null ? false : areas.some((area) => area.hatch && containsPoint(area.rings, coordinate));
  const outside = coordinate !== null && (coordinate[0] < -125 || coordinate[0] > -66 || coordinate[1] < 24 || coordinate[1] > 50);
  const headline = current ? highest?.description || highest?.label || "No areas outlined" : query.isPending ? "Loading current outlook…" : query.isError && outlook === undefined ? "Outlook unavailable" : "No current outlook available";
  const legend = ranked.filter((area, index) => ranked.findIndex((item) => item.label === area.label) === index).reverse();
  return <section className={styles.briefing} aria-labelledby="spc-briefing-title">
    <header className={styles.header}>
      <div className={styles.heading}><span className={styles.headingIcon}><ShieldAlert className="icon" aria-hidden="true" /></span><div><div className={styles.eyebrow}>Storm Prediction Center · {outlookDay}</div><h2 id="spc-briefing-title">Current convective outlook</h2></div></div>
      <div className={styles.products} role="group" aria-label="SPC outlook product">{OUTLOOK_KEYS.map((key) => <button key={key} type="button" aria-pressed={product === key} onClick={() => { setProduct(key); }}>{PRODUCTS[key]}</button>)}</div>
      <div className={styles.currentTime}><time dateTime={new Date(now).toISOString()}>{utc.format(now)} UTC</time><IconAction icon={RefreshCw} label="Refresh current outlook" loading={query.isFetching} disabled={query.isFetching} onClick={() => { setNow(Date.now()); void query.refetch(); }} /></div>
    </header>
    <div className={styles.body}>
      <SpcBriefingMap areas={areas} point={outside ? null : coordinate} label={`SPC ${PRODUCTS[product]} outlook${current ? ": current valid period" : ": no current data"}`} />
      <div className={styles.summary} aria-live="polite" aria-busy={query.isPending}>
        <div className={styles.riskHeading}><span>{current ? "Highest outlined area" : "Current guidance"}</span><span className={styles.badge} data-current={current}>{current ? "Current" : query.isPending ? "Checking" : "Unavailable"}</span></div>
        <h3>{highest === undefined ? null : <i className={styles.swatch} style={{ "--risk-color": highest.fill ?? highest.stroke } as CSSProperties} aria-hidden="true" />}{headline}</h3>
        {current ? <dl className={styles.times}>
          <div><dt>Issued</dt><dd>{outlook.issued === null ? "Not supplied" : <time dateTime={outlook.issued}>{utc.format(Date.parse(outlook.issued))} UTC</time>}</dd></div>
          <div><dt>Valid from</dt><dd><time dateTime={outlook.validFrom ?? undefined}>{utc.format(Date.parse(outlook.validFrom ?? ""))} UTC</time></dd></div>
          <div><dt>Valid until</dt><dd><time dateTime={outlook.validTo ?? undefined}>{utc.format(Date.parse(outlook.validTo ?? ""))} UTC</time></dd></div>
        </dl> : <p className={styles.unavailable}>{query.isPending ? "Fetching the latest published outlook for the current UTC time." : query.isError ? "SPC could not be reached. Refresh to try again." : "A valid outlook for the current UTC time was not returned. Refresh to check again."}</p>}
        <div className={styles.location}><MapPin className="icon-sm" aria-hidden="true" /><div><span>{coordinate === null ? "Your location" : locationName}</span><strong>{coordinate === null ? "Select a point or station on the map" : outside ? "Outside the contiguous U.S. outlook" : !current ? "Current risk unavailable" : atPoint?.description || atPoint?.label || "No outlined area at this point"}</strong>{qualifier ? <small>Significant-severe area also outlined</small> : null}</div></div>
        {query.isError && current ? <p className={styles.notice} role="status">Refresh failed. Showing the last valid outlook.</p> : null}
        <p className={styles.note}>{product === "cat" ? "Thunderstorm areas are separate from the five severe-risk categories." : "Probabilities describe the chance of a hazard within 25 miles of a point."} Outlooks are guidance; check local warnings.</p>
      </div>
    </div>
    <footer className={styles.footer}>
      <div className={styles.legend} aria-label="Outlined areas">{legend.length === 0 ? <span>Updated every 5 minutes while open</span> : legend.map((area) => <span key={area.label}><i className={styles.swatch} style={{ "--risk-color": area.fill ?? area.stroke } as CSSProperties} aria-hidden="true" />{area.label}</span>)}</div>
      <a href="https://www.spc.noaa.gov/products/outlook/day1otlk.html" target="_blank" rel="noopener noreferrer">NOAA / NWS SPC <ExternalLink className="icon-sm" aria-hidden="true" /></a>
    </footer>
  </section>;
}

import { ExternalLink, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useLatestSpcOutlook } from "../../map/mapQueries";
import { OUTLOOK_DAYS, OUTLOOK_KEYS, type OutlookDay, type OutlookProductKey } from "../../types/map";
import { IconAction } from "../../ui/Controls";
import styles from "./SpcBriefing.module.css";

const PRODUCTS: Record<OutlookProductKey, string> = { cat: "Categorical", torn: "Tornado", wind: "Wind", hail: "Hail", prob: "Severe probability" };
const PRODUCTS_BY_DAY: Record<OutlookDay, readonly OutlookProductKey[]> = { 1: OUTLOOK_KEYS, 2: OUTLOOK_KEYS, 3: ["cat", "prob"] };
const utc = new Intl.DateTimeFormat("en-GB", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
const EMPTY_AREAS = [] as const;

export function SpcBriefing(): ReactNode {
  const [day, setDay] = useState<OutlookDay>(1);
  const [product, setProduct] = useState<OutlookProductKey>("cat");
  const [now, setNow] = useState(Date.now);
  const dayLabel = `Day ${String(day)}`;
  const query = useLatestSpcOutlook(day, product);
  useEffect(() => {
    const update = (): void => { setNow(Date.now()); };
    const timer = window.setInterval(update, 30_000);
    window.addEventListener("focus", update);
    document.addEventListener("visibilitychange", update);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", update); document.removeEventListener("visibilitychange", update); };
  }, []);
  const outlook = query.data?.outlook;
  const validFrom = outlook?.validFrom ?? null;
  const validTo = outlook?.validTo ?? null;
  const start = Date.parse(validFrom ?? "");
  const end = Date.parse(validTo ?? "");
  const available = outlook != null && validFrom !== null && validTo !== null && start < end && now < end
    && (outlook.issued === null || Date.parse(outlook.issued) <= now);
  const current = available && start <= now;
  const areas = available ? outlook.areas : EMPTY_AREAS;
  const ranked = useMemo(() => [...areas].filter((area) => !area.hatch).sort((a, b) => b.rank - a.rank), [areas]);
  const highest = ranked[0];
  const states = outlook?.affectedStates;
  const affectedAreas = highest === undefined ? "None" : states === undefined ? "Unavailable" : states.join(", ") || "None";
  const headline = available ? highest?.description || highest?.label || "No areas outlined" : query.isPending ? `Loading ${dayLabel} outlook…` : "Outlook unavailable";
  const legend = ranked.filter((area, index) => ranked.findIndex((item) => item.label === area.label) === index).reverse();
  return <section className={styles.briefing} aria-labelledby="spc-briefing-title">
    <header className={styles.header}>
      <div className={styles.heading}>
        <div className={styles.eyebrow}>Storm Prediction Center</div>
        <h2 id="spc-briefing-title">Convective outlook</h2>
      </div>
      <div className={styles.controls}>
        <div className={styles.days} role="group" aria-label="SPC outlook day">
          {OUTLOOK_DAYS.map((option) => <button key={option} type="button" aria-pressed={day === option} onClick={() => {
            setDay(option);
            if (!PRODUCTS_BY_DAY[option].includes(product)) setProduct("cat");
          }}>Day {option}</button>)}
        </div>
        <div className={styles.products} role="group" aria-label="SPC outlook product">
          {PRODUCTS_BY_DAY[day].map((key) => <button key={key} type="button" aria-pressed={product === key} onClick={() => { setProduct(key); }}>{PRODUCTS[key]}</button>)}
        </div>
      </div>
      <div className={styles.currentTime}><time dateTime={new Date(now).toISOString()}>{utc.format(now)} UTC</time><IconAction icon={RefreshCw} label={`Refresh ${dayLabel} outlook`} loading={query.isFetching} disabled={query.isFetching} onClick={() => { setNow(Date.now()); void query.refetch(); }} /></div>
    </header>
    <div className={styles.summary} aria-live="polite" aria-busy={query.isPending}>
      <div className={styles.risk}>
        <div className={styles.riskHeading}><span>{available ? "Highest outlined area" : "Outlook guidance"}</span><span className={styles.badge} data-status={current ? "current" : available ? "upcoming" : undefined}>{current ? "Current" : available ? "Upcoming" : query.isPending ? "Checking" : "Unavailable"}</span></div>
        <h3>{highest === undefined ? null : <i className={styles.swatch} style={{ "--risk-color": highest.fill ?? highest.stroke } as CSSProperties} aria-hidden="true" />}{headline}</h3>
      </div>
      {available ? <dl className={styles.times}>
        <div><dt>Issued</dt><dd>{outlook.issued === null ? "Not supplied" : <time dateTime={outlook.issued}>{utc.format(Date.parse(outlook.issued))} UTC</time>}</dd></div>
        <div><dt>Valid from</dt><dd><time dateTime={validFrom}>{utc.format(start)} UTC</time></dd></div>
        <div><dt>Valid until</dt><dd><time dateTime={validTo}>{utc.format(end)} UTC</time></dd></div>
      </dl> : <p className={styles.unavailable}>{query.isPending ? `Fetching the latest SPC ${dayLabel} outlook.` : query.isError ? "SPC could not be reached. Refresh to try again." : `SPC returned no active or upcoming outlook for ${dayLabel}.`}</p>}
      {available ? <div className={styles.affected}>
        <span>Affected areas</span>
        <strong>{affectedAreas}</strong>
      </div> : null}
      {query.isError && available ? <p className={styles.notice} role="status">Refresh failed. Showing the last valid outlook.</p> : null}
      <p className={styles.note}>{product === "cat" ? "Thunderstorm areas are separate from the five severe-risk categories." : product === "prob" ? "Combined probabilities cover tornadoes, damaging winds, or large hail within 25 miles of a point." : "Probabilities describe the chance of this hazard within 25 miles of a point."} Check local warnings for immediate hazards.</p>
    </div>
    <footer className={styles.footer}>
      <div className={styles.legend} aria-label="Outlined areas">{legend.length === 0 ? <span>Updated every 5 minutes while open</span> : legend.map((area) => <span key={area.label}><i className={styles.swatch} style={{ "--risk-color": area.fill ?? area.stroke } as CSSProperties} aria-hidden="true" />{area.label}</span>)}</div>
      <a href={`https://www.spc.noaa.gov/products/outlook/day${String(day)}otlk.html`} target="_blank" rel="noopener noreferrer">NOAA / NWS SPC <ExternalLink className="icon-sm" aria-hidden="true" /></a>
    </footer>
  </section>;
}

import { CloudHail, CloudLightning, CloudRain, ExternalLink, Layers, Percent, RefreshCw, Snowflake, Tornado, Weight, Wind, type LucideIcon } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useLatestSpcOutlook, useLatestWpcOutlook } from "../../map/mapQueries";
import {
  OUTLOOK_DAYS,
  OUTLOOK_KEYS,
  WPC_WINTER_PRODUCTS,
  WPC_WINTER_PRODUCT_LABELS,
  type OutlookDay,
  type OutlookProductKey,
  type WpcWinterProduct,
} from "../../types/map";
import { IconAction } from "../../ui/Controls";
import { OutlookDiscussionSummary } from "./OutlookDiscussionSummary";
import styles from "./OutlookBriefing.module.css";

export type OutlookBriefingKind = "convective" | "ero" | "winter";

const KINDS: readonly { key: OutlookBriefingKind; label: string; icon: LucideIcon }[] = [
  { key: "convective", label: "Convective", icon: CloudLightning },
  { key: "ero", label: "Excessive Rainfall", icon: CloudRain },
  { key: "winter", label: "Winter Weather", icon: Snowflake },
];

const SPC_PRODUCTS: Record<OutlookProductKey, { label: string; icon: LucideIcon }> = {
  cat: { label: "Categorical", icon: Layers },
  torn: { label: "Tornado", icon: Tornado },
  wind: { label: "Wind", icon: Wind },
  hail: { label: "Hail", icon: CloudHail },
  prob: { label: "Severe probability", icon: Percent },
};
const SPC_PRODUCTS_BY_DAY: Record<OutlookDay, readonly OutlookProductKey[]> = { 1: OUTLOOK_KEYS, 2: OUTLOOK_KEYS, 3: ["cat", "prob"] };
const WINTER_PRODUCT_ICONS: Record<WpcWinterProduct, LucideIcon> = {
  "winter-overall": Layers,
  "winter-snow": Snowflake,
  "winter-snow-load": Weight,
  "winter-ice": CloudHail,
  "winter-blowing-snow": Wind,
};
const utc = new Intl.DateTimeFormat("en-GB", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "UTC" });
const EMPTY_AREAS = [] as const;

export function OutlookBriefing({ initialKind = "convective" }: { initialKind?: OutlookBriefingKind }): ReactNode {
  const [kind, setKind] = useState<OutlookBriefingKind>(initialKind);
  const [day, setDay] = useState<OutlookDay>(1);
  const [spcProduct, setSpcProduct] = useState<OutlookProductKey>("cat");
  const [winterProduct, setWinterProduct] = useState<WpcWinterProduct>("winter-overall");
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    const update = (): void => { setNow(Date.now()); };
    const timer = window.setInterval(update, 30_000);
    window.addEventListener("focus", update);
    document.addEventListener("visibilitychange", update);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", update); document.removeEventListener("visibilitychange", update); };
  }, []);

  const spcQuery = useLatestSpcOutlook(day, spcProduct, kind === "convective");
  const wpcQuery = useLatestWpcOutlook(
    day,
    kind !== "convective",
    kind === "winter" ? winterProduct : "ero",
  );
  const query = kind === "convective" ? spcQuery : wpcQuery;

  const dayLabel = `Day ${String(day)}`;
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

  const agency = kind === "convective" ? "Storm Prediction Center" : "Weather Prediction Center";
  const title = kind === "convective" ? "Convective outlook" : kind === "ero" ? "Excessive rainfall outlook" : "Winter weather outlook";
  const sourceUrl = kind === "convective"
    ? `https://www.spc.noaa.gov/products/outlook/day${String(day)}otlk.html`
    : outlook?.sourceUrl || (kind === "winter"
      ? "https://www.wpc.ncep.noaa.gov/wwd/wssi/wssi.php"
      : `https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=curr&day=${String(day)}`);
  const agencyShort = kind === "convective" ? "NOAA / NWS SPC" : "NOAA / NWS WPC";
  const refreshLabel = kind === "convective" ? `Refresh ${dayLabel} outlook` : `Refresh ${dayLabel} WPC outlook`;

  const note = kind === "convective"
    ? (spcProduct === "cat"
      ? "Thunderstorm areas are separate from the five severe-risk categories."
      : spcProduct === "prob"
        ? "Combined probabilities cover tornadoes, damaging winds, or large hail within 25 miles of a point."
        : "Probabilities describe the chance of this hazard within 25 miles of a point.")
      + " Check local warnings for immediate hazards."
    : kind === "ero"
      ? "Excessive rainfall probabilities represent the chance of rainfall exceeding flash flood guidance within 25 miles of a point. Check local warnings for immediate flood hazards."
      : "Winter Storm Severity Index levels estimate potential societal impacts from snow, ice, and blowing snow. Check local winter warnings for specific advisories.";

  return (
    <section className={styles.briefing} aria-labelledby="outlook-briefing-title">
      <div className={styles.topBar}>
        <div className={styles.kinds} role="group" aria-label="Outlook category">
          {KINDS.map((option) => (
            <IconAction
              key={option.key}
              className={styles.iconChoice}
              icon={option.icon}
              label={option.label}
              size="sm"
              hintSide="bottom"
              aria-pressed={kind === option.key}
              onClick={() => { setKind(option.key); }}
            />
          ))}
        </div>
        <div className={styles.currentTime}>
          <time dateTime={new Date(now).toISOString()} title="Current time">{utc.format(now)} UTC</time>
          <IconAction
            icon={RefreshCw}
            size="sm"
            label={refreshLabel}
            loading={query.isFetching}
            disabled={query.isFetching}
            onClick={() => { setNow(Date.now()); void query.refetch(); }}
          />
        </div>
      </div>

      <header className={styles.header}>
        <div className={styles.heading}>
          <h2 id="outlook-briefing-title">{title}</h2>
          <p className={styles.agency}>{agency}</p>
        </div>
        <div className={styles.controls}>
          <div
            className={styles.segments}
            role="group"
            aria-label={kind === "convective" ? "SPC outlook day" : kind === "ero" ? "WPC outlook day" : "WPC winter outlook day"}
          >
            {OUTLOOK_DAYS.map((option) => (
              <button
                key={option}
                className={styles.day}
                type="button"
                aria-pressed={day === option}
                onClick={() => {
                  setDay(option);
                  if (kind === "convective" && !SPC_PRODUCTS_BY_DAY[option].includes(spcProduct)) setSpcProduct("cat");
                }}
              >
                Day {option}
              </button>
            ))}
          </div>
          {kind === "convective" ? (
            <div className={styles.segments} role="group" aria-label="SPC outlook product">
              {SPC_PRODUCTS_BY_DAY[day].map((key) => (
                <IconAction
                  key={key}
                  className={styles.iconChoice}
                  icon={SPC_PRODUCTS[key].icon}
                  label={SPC_PRODUCTS[key].label}
                  size="sm"
                  hintSide="bottom"
                  aria-pressed={spcProduct === key}
                  onClick={() => { setSpcProduct(key); }}
                />
              ))}
            </div>
          ) : kind === "winter" ? (
            <div className={styles.segments} role="group" aria-label="WPC winter outlook product">
              {WPC_WINTER_PRODUCTS.map((option) => (
                <IconAction
                  key={option}
                  className={styles.iconChoice}
                  icon={WINTER_PRODUCT_ICONS[option]}
                  label={WPC_WINTER_PRODUCT_LABELS[option].label}
                  size="sm"
                  hintSide="bottom"
                  aria-pressed={winterProduct === option}
                  onClick={() => { setWinterProduct(option); }}
                />
              ))}
            </div>
          ) : null}
        </div>
      </header>

      <div className={styles.summary} aria-live="polite" aria-busy={query.isPending}>
        <div className={styles.risk} data-available={available} style={highest === undefined ? undefined : ({ "--risk-color": highest.fill ?? highest.stroke } as CSSProperties)}>
          {available ? <p className={styles.riskLabel}>Highest outlined area</p> : null}
          <div className={styles.riskHeadline}>
            <h3>{headline}</h3>
            {available ? <span className={styles.status} data-current={current}>{current ? "Current" : "Upcoming"}</span> : null}
          </div>
          {available ? (
            <dl className={styles.affected}>
              <dt>Affected areas</dt>
              <dd>{affectedAreas}</dd>
            </dl>
          ) : (
            <p className={styles.unavailable}>
              {query.isPending
                ? `Fetching the latest ${agencyShort.replace("NOAA / NWS ", "")} ${dayLabel} outlook.`
                : query.isError
                  ? `${kind === "convective" ? "SPC" : "WPC"} could not be reached. Refresh to try again.`
                  : `${kind === "convective" ? "SPC" : "WPC"} returned no active or upcoming outlook for ${dayLabel}.`}
            </p>
          )}
        </div>
        {available ? (
          <dl className={styles.times}>
            <div><dt>Issued</dt><dd>{outlook.issued === null ? "Not supplied" : <time dateTime={outlook.issued}>{utc.format(Date.parse(outlook.issued))} UTC</time>}</dd></div>
            <div><dt>Valid from</dt><dd><time dateTime={validFrom}>{utc.format(start)} UTC</time></dd></div>
            <div><dt>Valid until</dt><dd><time dateTime={validTo}>{utc.format(end)} UTC</time></dd></div>
          </dl>
        ) : null}
        <OutlookDiscussionSummary discussion={available ? outlook.discussion : undefined} />
        {query.isError && available ? <p className={styles.notice} role="status">Refresh failed. Showing the last valid outlook.</p> : null}
        <p className={styles.note}>{note}</p>
      </div>

      <footer className={styles.footer}>
        <div className={styles.legend} aria-label="Outlined areas">
          {legend.length === 0 ? <span>Updated every 5 minutes while open</span> : legend.map((area) => (
            <span key={area.label}>
              <i className={styles.swatch} style={{ "--risk-color": area.fill ?? area.stroke } as CSSProperties} aria-hidden="true" />
              {area.label}
            </span>
          ))}
        </div>
        <a href={sourceUrl} target="_blank" rel="noopener noreferrer">{agencyShort} <ExternalLink className="icon-sm" aria-hidden="true" /></a>
      </footer>
    </section>
  );
}

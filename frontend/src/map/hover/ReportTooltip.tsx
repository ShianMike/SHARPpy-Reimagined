import { Fragment, useLayoutEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { Surface } from "../../ui/Surface";
import { IconAction } from "../../ui/Controls";
import { cx } from "../../lib/cx";
import { convertUnit, displayReading, displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { SPOTTER_MARKER_LABELS, spotterMarkerKind } from "../layers/SpotterLayer";
import { useHoverStore } from "./hoverStore";
import styles from "./StationTooltip.module.css";

const COMPASS_POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"] as const;

export function ReportTooltip(): ReactNode {
  const units = useUiStore((state) => state.units);
  const pinned = useHoverStore((state) => state.pinnedSpotter);
  const report = useHoverStore((state) => state.pinnedSpotter === null ? state.report : null);
  const ref = useRef<HTMLElement | null>(null);
  const closeButton = useRef<HTMLButtonElement | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const anchor = pinned ?? report;

  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null || anchor === null) {
      return;
    }
    const box = element.getBoundingClientRect();
    element.style.setProperty("--tooltip-x", `${String(anchor.x)}px`);
    element.style.setProperty("--tooltip-y", `${String(anchor.y)}px`);
    element.style.setProperty("--tooltip-w", `${String(box.width)}px`);
    element.style.setProperty("--tooltip-h", `${String(box.height)}px`);
  });

  useLayoutEffect(() => {
    if (pinned === null || closeButton.current === null) return;
    const previous = document.activeElement;
    if (previous instanceof HTMLElement && previous !== closeButton.current) returnFocus.current = previous;
    closeButton.current.focus({ preventScroll: true });
  }, [pinned]);

  const spotter = pinned?.position ?? report?.source?.position;
  if (spotter !== undefined) {
    const kind = SPOTTER_MARKER_LABELS[spotterMarkerKind(spotter)];
    const title = spotter.displayName ?? kind;
    const motion = spotter.motion;
    const heading = motion?.kind === "heading" ? COMPASS_POINTS[Math.round(motion.degrees / 22.5) % COMPASS_POINTS.length] : undefined;
    const close = (): void => {
      useHoverStore.setState({ pinnedSpotter: null, report: null });
      returnFocus.current?.focus({ preventScroll: true });
    };
    return <Surface ref={ref} elevation="popover" role={pinned === null ? "tooltip" : "dialog"} aria-label={pinned === null ? undefined : `Spotter details for ${title}`}
      className={cx(styles.tooltip, styles.report, styles.spotterCard, pinned === null ? undefined : styles.pinnedSpotterCard)}
      data-testid={pinned === null ? "report-tooltip" : "spotter-details"}
      onKeyDown={(event) => {
        if (pinned !== null && event.key === "Escape") { event.stopPropagation(); close(); }
      }}>
      <header className={styles.spotterHeading}>
        <div className={styles.reportHeading}>
          <p className={styles.reportTitle} dir="auto">{title}</p>
          <p className={styles.meta}>{spotter.displayName === undefined ? "Spotter Network" : `${kind} · Spotter Network`}</p>
        </div>
        {pinned === null ? null : <IconAction ref={closeButton} icon={X} size="sm" label="Close spotter details" onClick={close} />}
      </header>
      <dl className={styles.reportFacts}>
        <dt>Last position</dt><dd><time dateTime={spotter.updatedAt} title={spotter.updatedAt.replace("T", " ").replace(/Z$/u, " UTC")}>
          {spotter.updatedAt.slice(11, 19)} UTC
        </time></dd>
        {motion === undefined ? null : <><dt>{motion.kind === "stationary" ? "Movement" : "Heading"}</dt>
          <dd>{motion.kind === "stationary" ? "Stationary" : `${heading ?? ""} ${String(motion.degrees)}°`}</dd></>}
      </dl>
    </Surface>;
  }

  if (report === null) {
    return null;
  }
  const lines = report.description.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const eventIndex = lines.findIndex((line) => line.startsWith("Event time:"));
  const stationBreak = eventIndex < 0 ? report.label.indexOf(" — ") : -1;
  const title = eventIndex < 0 ? (stationBreak < 0 ? report.label : report.label.slice(0, stationBreak)) : displayReading(report.label, units);
  const location = eventIndex > 0 ? lines.slice(0, eventIndex).join(" ")
    : stationBreak < 0 ? "" : report.label.slice(stationBreak + 3);
  const facts: { name: string; value: string }[] = [];
  const readings: { name: string; value: string }[] = [];
  const attribution: string[] = [];
  const paragraphs: string[] = [];
  if (report.observation !== undefined) {
    const unit = displayUnit("°C", units);
    for (const [name, value] of [["Temperature", report.observation.temperatureC], ["Dewpoint", report.observation.dewpointC]] as const) {
      readings.push({ name, value: value === null ? "missing" : `${String(Number(convertUnit(value, "°C", unit).toFixed(1)))} ${unit}` });
    }
  }
  for (const line of lines.slice(Math.max(0, eventIndex))) {
    const observed = eventIndex < 0 ? /^Observed (.+?)(?: \(([^()]*)\))?$/.exec(line) : null;
    if (observed?.[1] !== undefined) {
      facts.push({ name: "Observed", value: observed[1] });
      if (observed[2] !== undefined) facts.push({ name: "Time offset", value: observed[2] });
      continue;
    }
    if (eventIndex < 0 && line.startsWith("Temperature ")) {
      // Only the first two separators divide readings; later ones belong to the wind's status text.
      const [upper, lower, ...rest] = line.split(/;\s*/);
      for (const reading of report.observation === undefined ? [upper, lower, rest.join("; ")] : [rest.join("; ")]) {
        if (reading === undefined || reading === "") continue;
        const split = reading.indexOf(" ");
        if (split < 1) paragraphs.push(reading);
        else readings.push({ name: reading.charAt(0).toUpperCase() + reading.slice(1, split), value: displayReading(reading.slice(split + 1), units) });
      }
      continue;
    }
    const field = /^(Event time|Magnitude|Source|Issuing office|Provider):\s*(.+)$/.exec(line);
    const name = field?.[1], value = field?.[2];
    if (name === undefined || value === undefined) paragraphs.push(line);
    else if (name === "Issuing office" || name === "Provider" || (eventIndex < 0 && name === "Source")) attribution.push(line);
    else if (name !== "Magnitude" || !report.label.includes(value)) facts.push({ name, value: name === "Magnitude" ? displayReading(value, units) : value });
  }
  return (
    <Surface ref={ref} elevation="popover" role="tooltip" className={cx(styles.tooltip, styles.report)} data-testid="report-tooltip">
      <header className={styles.reportHeading}>
        <p className={styles.reportTitle}>{title}</p>
        {location === "" ? null : <p className={styles.meta}>{location}</p>}
      </header>
      {[facts, readings].map((section, index) => section.length === 0 ? null : <dl key={index} className={styles.reportFacts}>
        {section.map(({ name, value }, fieldIndex) => <Fragment key={fieldIndex}><dt>{name}</dt><dd>{value}</dd></Fragment>)}
      </dl>)}
      {paragraphs.length === 0 ? null : <div className={styles.reportNarrative}>
        {paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
      </div>}
      {attribution.length === 0 ? null : <footer className={styles.reportAttribution}>
        {attribution.map((line, index) => <p key={index}>{line}</p>)}
      </footer>}
    </Surface>
  );
}

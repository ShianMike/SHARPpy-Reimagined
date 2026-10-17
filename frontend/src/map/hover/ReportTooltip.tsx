import { Fragment, useLayoutEffect, useRef, type ReactNode } from "react";
import { Surface } from "../../ui/Surface";
import { convertUnit, displayReading, displayUnit } from "../../lib/displayUnits";
import { useUiStore } from "../../stores/uiStore";
import { useHoverStore } from "./hoverStore";
import styles from "./StationTooltip.module.css";

export function ReportTooltip(): ReactNode {
  const units = useUiStore((state) => state.units);
  const report = useHoverStore((state) => state.report);
  const ref = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null || report === null) {
      return;
    }
    const box = element.getBoundingClientRect();
    element.style.setProperty("--tooltip-x", `${String(report.x)}px`);
    element.style.setProperty("--tooltip-y", `${String(report.y)}px`);
    element.style.setProperty("--tooltip-w", `${String(box.width)}px`);
    element.style.setProperty("--tooltip-h", `${String(box.height)}px`);
  });

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
    <Surface ref={ref} elevation="popover" role="tooltip" className={[styles.tooltip, styles.report].join(" ")} data-testid="report-tooltip">
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

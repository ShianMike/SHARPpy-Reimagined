import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import type { OutlookDiscussion } from "../../types/map";
import styles from "./OutlookDiscussionSummary.module.css";

interface OutlookDiscussionSummaryProps {
  discussion?: OutlookDiscussion | undefined;
  compact?: boolean;
}

const PRESERVED_TOKENS = new Set([
  "US", "USA", "CONUS", "SPC", "WPC", "WSSI", "WSSI-P", "NWS", "NOAA", "UTC", "GMT",
  "IVT", "AR", "SE", "SW", "NE", "NW", "N", "S", "E", "W",
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
  "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
  "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
  "VA", "WA", "WV", "WI", "WY", "DC",
]);

function formatHeadline(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed === "" || /[a-z]/u.test(trimmed)) return trimmed;
  const words = trimmed.split(/\s+/u);
  return words.map((token, index) => {
    const match = /^([^A-Za-z0-9]*)([A-Za-z0-9/-]+)([^A-Za-z0-9]*)$/u.exec(token);
    if (match === null) return token.toLowerCase();
    const [, lead = "", core = "", trail = ""] = match;
    const upper = core.toUpperCase();
    if (PRESERVED_TOKENS.has(upper)) return `${lead}${upper}${trail}`;
    const lower = core.toLowerCase();
    const text = index === 0 ? `${lower.charAt(0).toUpperCase()}${lower.slice(1)}` : lower;
    return `${lead}${text}${trail}`;
  }).join(" ");
}

export function OutlookDiscussionSummary({ discussion, compact = false }: OutlookDiscussionSummaryProps): ReactNode {
  const [open, setOpen] = useState(false);
  const [selectedHeading, setSelectedHeading] = useState("all");
  const bodyId = useId();

  if (discussion === undefined) return null;
  const headline = formatHeadline(discussion.headline);
  const showHeadline = headline !== "" && headline !== discussion.summary;
  const showSummary = discussion.summary !== "";
  if (!showHeadline && !showSummary && discussion.sections.length === 0) return null;

  const sections = discussion.sections;
  const showSectionFilters = compact && sections.length > 1;
  const activeHeading = showSectionFilters && selectedHeading !== "all" && sections.some((section) => section.heading === selectedHeading)
    ? selectedHeading
    : "all";
  const visibleSections = activeHeading === "all"
    ? sections
    : sections.filter((section) => section.heading === activeHeading);
  const Label = compact ? "span" : "h3";
  const expandLabel = compact
    ? `Read full discussion · ${String(sections.length)} ${sections.length === 1 ? "section" : "sections"}`
    : "Read full discussion";

  return (
    <div className={cx(styles.discussion, compact ? styles.compact : styles.overviewDiscussion)}>
      <div className={styles.header}>
        <Label className={styles.label}>Forecast discussion</Label>
        {sections.length === 0 ? null : (
          <button
            type="button"
            className={styles.toggle}
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => { setOpen((wasOpen) => !wasOpen); }}
          >
            {open ? "Hide full discussion" : expandLabel}
            {compact ? null : <ChevronDown className="icon-sm" aria-hidden="true" />}
          </button>
        )}
      </div>
      {showHeadline ? <p className={styles.headline}>{headline}</p> : null}
      {showSummary ? <p className={styles.summary}>{discussion.summary}</p> : null}
      {sections.length === 0 ? null : (
        <div id={bodyId} className={styles.expanded} data-motion="disclosure" hidden={!open} inert={!open}>
          {showSectionFilters ? (
            <div className={styles.sectionFilters} role="group" aria-label="Discussion sections">
              <button
                type="button"
                aria-pressed={activeHeading === "all"}
                onClick={() => { setSelectedHeading("all"); }}
              >
                All ({String(sections.length)})
              </button>
              {sections.map((section, index) => (
                <button
                  key={`${section.heading}-${String(index)}`}
                  type="button"
                  aria-pressed={activeHeading === section.heading}
                  onClick={() => { setSelectedHeading(section.heading); }}
                >
                  {section.heading}
                </button>
              ))}
            </div>
          ) : null}
          <div className={styles.sections}>
            {visibleSections.map((section, index) => (
              <section className={styles.section} key={`${section.heading}-${String(index)}`}>
                <h4>{section.heading}</h4>
                {section.body.split("\n\n").map((paragraph, pIndex) => (
                  <p key={pIndex}>{paragraph}</p>
                ))}
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

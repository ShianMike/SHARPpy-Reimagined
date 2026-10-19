import { Check, Download, Info, Square, TriangleAlert, X } from "lucide-react";
import { useState } from "react";
import { Popover } from "../../components/Popover";
import { cx } from "../../lib/cx";
import type { useFrameDownload } from "../../map/useFrameDownload";
import { Action, IconAction } from "../../ui/Controls";
import hints from "../../ui/HoverHint.module.css";
import styles from "./TimelineRegion.module.css";
import panel from "./ForecastDownloads.module.css";

export function ForecastDownloads({ download }: { download: ReturnType<typeof useFrameDownload> }) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const Icon = download.running ? Square : download.complete ? Check : download.failed ? TriangleAlert : Download;
  const hours = (values: number[]): string => values.slice(0, 8).map((hour) => `F${String(hour).padStart(3, "0")}`).join(", ") + (values.length > 8 ? ` +${String(values.length - 8)} more` : "");
  return <>
    <Action className={cx(styles.option, styles.download, hints.target)} aria-label={download.running ? "Stop downloading forecast hours" : "Download all forecast hours"}
      aria-description={download.message} data-active={download.running || download.complete} data-failed={download.failed && !download.running}
      disabled={download.disabled} onClick={download.toggle}>
      <Icon className="icon" aria-hidden="true" />
      <span className={styles.downloadLabel}>{download.running ? "Loading" : download.complete ? "Saved" : download.failed ? "Retry" : "Load hours"}</span>
      {download.started ? <span className={styles.count}>{download.running ? download.loaded : download.cached}<span>/</span>{download.total}</span> : null}
      {download.running ? <progress className={styles.progress} aria-label="Downloaded forecast hours" max={download.total} value={download.loaded} /> : null}
      <span className={hints.hint} data-side="top" aria-hidden="true">{download.message}</span>
    </Action>
    <IconAction icon={Info} size="sm" label="Forecast download details" hintSide="top" aria-expanded={open} onClick={(event) => { setAnchor(event.currentTarget); setOpen(!open); }} />
    <Popover open={open} anchor={anchor} onClose={() => { setOpen(false); }} role="dialog" label="Forecast downloads" initialFocus="container" align="end" className={panel.details}>
      <div className={panel.header}><h3>Forecast downloads</h3><IconAction icon={X} size="sm" label="Close download details" onClick={() => { setOpen(false); }} /></div>
      <dl className={panel.stats}>
        <div><dt>Saved to cache</dt><dd>{download.cacheError ? "Unavailable" : `${String(download.cached)} / ${String(download.total)}`}</dd></div>
        <div><dt>Ready in memory</dt><dd>{download.ready} / {download.total}</dd></div>
      </dl>
      <p className={panel.hint}>Saved hours can be reused without downloading. Playback keeps a rolling set in memory and loads other saved hours as needed.</p>
      {download.failedHours.length > 0 ? <p className={panel.problem}>Failed: {hours(download.failedHours)}</p>
        : download.missing.length > 0 ? <p className={panel.hint}>Not saved: {hours(download.missing)}</p> : null}
      <div className={panel.actions}><Action onClick={download.refresh}>Check cache</Action><Action tone="accent" disabled={download.disabled} onClick={download.toggle}>{download.running ? "Stop download" : download.failed ? "Retry missing hours" : "Load hours"}</Action></div>
    </Popover>
  </>;
}

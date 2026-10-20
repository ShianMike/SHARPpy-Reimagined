import { Check, ChevronDown, Download, HardDrive, MemoryStick, RotateCw, Square, TriangleAlert, X } from "lucide-react";
import { useId, useState } from "react";
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
  const id = useId();
  const problem = download.failed || download.cacheError;
  const Icon = download.running ? Download : problem ? TriangleAlert : download.complete ? Check : Download;
  const ActionIcon = download.running ? Square : download.failed ? RotateCw : Download;
  const status = download.disabled ? "Select A Forecast And Visible Map Layer To Load Its Hours."
    : download.running ? "Downloading Forecast Hours…"
    : download.cacheError ? "Couldn’t Check Saved Hours. Check The Cache To Try Again."
    : download.complete ? "All Hours Saved On This Device."
    : download.failed ? "Some Hours Couldn’t Load. Retry Below."
    : "Save This Run For Playback.";
  const listedHours = download.failedHours.length > 0 ? download.failedHours : download.missing;
  const hoursLabel = download.failedHours.length > 0 ? "Failed Hours" : "Hours To Load";
  return <>
    <Action className={cx(styles.option, styles.download, panel.trigger, hints.target)} aria-label="Forecast Hours"
      aria-description={download.disabled || download.cacheError ? status : download.message}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined}
      data-active={download.running || download.complete} data-failed={problem && !download.running}
      onClick={(event) => { setAnchor(event.currentTarget); setOpen(!open); }}>
      <Icon className="icon" aria-hidden="true" />
      <span className={styles.downloadLabel}>Forecast Hours</span>
      {download.total > 0 ? <span className={styles.count} aria-hidden="true">{download.cacheError && !download.running ? "—" : download.running ? download.loaded : download.cached}<span>/</span>{download.total}</span> : null}
      <ChevronDown className={cx("icon-sm", panel.chevron)} aria-hidden="true" />
      {download.running ? <progress className={styles.progress} aria-hidden="true" max={download.total} value={download.loaded} /> : null}
      {open ? null : <span className={hints.hint} data-side="top" aria-hidden="true">Manage Forecast Downloads</span>}
    </Action>
    <Popover id={id} open={open} anchor={anchor} onClose={() => { setOpen(false); }} role="dialog" label="Forecast Hours" initialFocus="container" align="end" side="top" className={panel.details}>
      <div className={panel.header}>
        <h3>Forecast Hours</h3>
        <div className={panel.headerActions}>
          <IconAction icon={RotateCw} size="sm" label="Check Cache" hintSide="bottom" disabled={download.disabled} onClick={download.refresh} />
          <IconAction icon={X} size="sm" label="Close Forecast Hours" onClick={() => { setOpen(false); anchor?.focus(); }} />
        </div>
      </div>
      <p className={panel.status} data-failed={problem && !download.running}>{status}</p>
      <div className={panel.summary}>
        <dl className={panel.saved}>
          <dt><HardDrive className="icon-sm" aria-hidden="true" />Saved On Device</dt>
          <dd>{download.cacheError ? "Unavailable" : <>{download.cached}<span> / {download.total}</span></>}</dd>
        </dl>
        {!download.cacheError && download.total > 0 ? <progress className={panel.progress} aria-label={download.running ? "Downloaded Forecast Hours" : "Saved Forecast Hours"} max={download.total} value={download.running ? download.loaded : download.cached} /> : null}
        {download.running ? <p className={panel.processed}>{download.loaded} / {download.total} Processed</p> : null}
        <dl className={panel.memory}><dt><MemoryStick className="icon-sm" aria-hidden="true" />Ready In Memory</dt><dd>{download.ready} / {download.total}</dd></dl>
        <p className={panel.hint}>Saved Hours Load Into Memory As Needed.</p>
      </div>
      {listedHours.length > 0 && !download.cacheError && !download.complete ? <details className={panel.hours} open={download.failedHours.length > 0}>
        <summary><span>{hoursLabel}</span><span className={panel.total}>{listedHours.length}</span><ChevronDown className="icon-sm" aria-hidden="true" /></summary>
        <div className={panel.hourList}>{listedHours.map((hour) => <span key={hour}>{`F${String(hour).padStart(3, "0")}`}</span>)}</div>
      </details> : null}
      {!download.complete || download.running ? <div className={panel.actions}>
        <Action className={panel.primary} tone={download.running ? "quiet" : "accent"} disabled={download.disabled} onClick={download.toggle}>
          <ActionIcon className="icon" aria-hidden="true" />{download.running ? "Stop Download" : download.failed ? "Retry Missing Hours" : "Load Hours"}
        </Action>
      </div> : null}
    </Popover>
  </>;
}

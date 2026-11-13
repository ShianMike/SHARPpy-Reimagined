import { ChevronDown, Clock3, GripHorizontal, Radio, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Popover } from "../../components/Popover";
import { cx } from "../../lib/cx";
import { usePanelRadar, useRadarFrame, useRadarSiteFrame } from "../../map/mapQueries";
import { LIVE_MINUTE, liveTimestamp, useLiveTrackingStore } from "../../stores/liveTrackingStore";
import { panelLayers, panelWeather, useMapStore } from "../../stores/mapStore";
import { Action, IconAction } from "../../ui/Controls";
import controls from "../toolbar/ForecastControls.module.css";
import styles from "./RadarScanPicker.module.css";
import hints from "../../ui/HoverHint.module.css";

export function RadarScanPicker({ onSelect }: { onSelect: (time: number) => void }): ReactNode {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const { now, selectedTime } = useLiveTrackingStore();
  const scope = useMapStore((state) => panelWeather(state).radarScope);
  const visible = useMapStore((state) => panelLayers(state, state.activePanel).radar.visible);
  const { site, product, tilt } = usePanelRadar();
  const mosaic = useRadarFrame(product, false, selectedTime === null ? null : liveTimestamp(selectedTime));
  const antenna = useRadarSiteFrame(product, site, false, selectedTime === null ? null : liveTimestamp(selectedTime), tilt);
  const radar = scope === "site" ? antenna : mosaic;
  const scanTime = Date.parse(radar.data?.validTime ?? "");
  const hasScan = Number.isFinite(scanTime);
  const scan = hasScan ? liveTimestamp(scanTime) : null;
  const age = hasScan ? Math.max(0, Math.floor((now - scanTime) / LIVE_MINUTE)) : 0;
  const close = (): void => { setOpen(false); anchor?.focus(); };
  const status = !visible ? "Radar layer hidden" : radar.isError ? hasScan ? "Update failed; showing the last loaded scan" : "Radar scan unavailable"
    : !hasScan ? radar.isFetching ? "Loading radar scan…" : radar.data === undefined ? "No radar scan loaded" : "Provider did not supply a scan time"
    : radar.isFetching ? "Updating automatically…" : selectedTime === null ? "Updates automatically" : "Historical scan";
  return <>
    <button type="button" className={cx(controls.trigger, hints.target)} aria-label="Radar scan time" aria-haspopup="dialog" aria-expanded={open}
      onClick={(event) => { setAnchor(event.currentTarget); setOpen(!open); }}>
      <Clock3 className="icon-sm" aria-hidden="true" /><span className={controls.triggerCopy}><span className={controls.name}>Radar time</span>
        <strong>{scan === null || !visible ? selectedTime === null ? "Live" : "History" : `${scan.slice(11, 16)} UTC`}</strong></span><ChevronDown className="icon-sm" aria-hidden="true" />
      <span className={hints.hint} data-side="bottom" aria-hidden="true">See the actual radar scan time.</span>
    </button>
    <Popover open={open} anchor={anchor} movable role="dialog" label="Radar time" initialFocus="first" width="content"
      className={controls.picker} onClose={() => { setOpen(false); }}>
      <header className={controls.dialogHeader} data-popover-drag>
        <IconAction icon={GripHorizontal} size="sm" label="Move popup" data-popover-move aria-description="Drag to move. Arrow keys move the popup; Home centers it on the screen." />
        <h2>Radar time</h2><span className={controls.badge}>{selectedTime === null ? "Live" : "History"}</span><IconAction icon={X} size="sm" label="Close radar time" onClick={close} />
      </header>
      <div className={controls.selectionSummary}>
        <span><strong>{selectedTime === null ? "Latest scan" : "Displayed scan"}</strong><small>{status}</small></span>
        <span><strong>{scan === null || !visible ? "—" : scan.replace("T", " ").replace("Z", " UTC")}</strong><small>{scan === null || !visible ? "Scan time unavailable" : "Actual scan · UTC"}</small></span>
      </div>
      <div className={cx(controls.dialogBody, styles.body)}>
        <p className={styles.scanTime} role="status">{!visible || scan === null ? status : selectedTime === null
          ? `${String(age)} min old${age > 10 ? " · Delayed scan" : ""}`
          : `Viewing ${liveTimestamp(selectedTime).replace("T", " ").replace("Z", " UTC")}`}</p>
      </div>
      {selectedTime === null ? null : <footer className={cx(controls.pickerFooter, styles.footer)}>
        <span>Use the timeline to review earlier weather.</span>
        <Action tone="accent" aria-label="Return to Live" onClick={() => { onSelect(now); close(); }}>
          <Radio className="icon-sm" aria-hidden="true" />Live
        </Action>
      </footer>}
    </Popover>
  </>;
}

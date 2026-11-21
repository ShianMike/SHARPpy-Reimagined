import { Download, GripHorizontal, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode, type SubmitEvent } from "react";
import { Popover } from "../../components/Popover";
import { ForecastDrawingDetailsFields } from "../../features/forecastExport/ForecastDrawingDetailsFields";
import { useMapStore } from "../../stores/mapStore";
import { useUiStore } from "../../stores/uiStore";
import { Action, IconAction } from "../../ui/Controls";
import { exportForecastDrawingPng } from "./exportForecastDrawing";
import styles from "./ForecastDrawingPngControl.module.css";

const DIALOG_ID = "forecast-drawing-export";

export function ForecastDrawingPngControl({ disabled, className, unavailableReason }: {
  disabled: boolean;
  className: string | undefined;
  unavailableReason?: string | undefined;
}): ReactNode {
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState(() => useUiStore.getState().forecastDrawingDetails);
  const [pending, setPending] = useState(false);
  const [downloaded, setDownloaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); }, []);

  const close = (restoreFocus: boolean): void => {
    request.current?.abort();
    request.current = null;
    setPending(false);
    setOpen(false);
    if (restoreFocus) anchor?.focus();
  };

  const download = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (disabled || request.current !== null) return;
    const job = new AbortController();
    request.current = job;
    useUiStore.getState().setForecastDrawingDetails(details);
    setPending(true); setDownloaded(false); setError(null);
    void exportForecastDrawingPng(useMapStore.getState().activePanel, job.signal).then(() => {
      if (!job.signal.aborted) setDownloaded(true);
    }, (failure: unknown) => {
      if (!job.signal.aborted) setError(failure instanceof Error ? failure.message : "Couldn’t export the outlook PNG.");
    }).finally(() => {
      if (request.current !== job) return;
      request.current = null;
      setPending(false);
    });
  };

  return <>
    <Action ref={setAnchor} className={className} disabled={disabled && !open} aria-label="Export outlook PNG"
      aria-description={unavailableReason} title={unavailableReason}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? DIALOG_ID : undefined}
      onClick={() => {
        if (open) { close(true); return; }
        setDetails(useUiStore.getState().forecastDrawingDetails);
        setDownloaded(false); setError(null); setOpen(true);
      }}>
      <Download className="icon-sm" aria-hidden="true" />Export PNG
    </Action>
    <Popover id={DIALOG_ID} open={open} anchor={anchor} onClose={() => { close(false); }} role="dialog"
      labelledBy={`${DIALOG_ID}-title`} initialFocus="first" movable backdrop className={styles.dialog}>
      <header className={styles.header} data-popover-drag>
        <IconAction icon={GripHorizontal} size="sm" label="Move outlook details" data-popover-move
          aria-description="Drag the header or use the arrow keys to move the popup." />
        <h2 id={`${DIALOG_ID}-title`}>Outlook details</h2>
        <IconAction icon={X} size="sm" label="Close outlook details" onClick={() => { close(true); }} />
      </header>
      <form className={styles.form} onSubmit={download}>
        <ForecastDrawingDetailsFields value={details} disabled={pending} onChange={(value) => { setDetails(value); setDownloaded(false); setError(null); }} />
        {error === null ? null : <p className={styles.error} role="alert">{error}</p>}
        <footer className={styles.footer}>
          <span className={styles.status} role="status">{pending ? "Preparing PNG…" : downloaded ? "PNG download started." : ""}</span>
          <Action type="submit" tone="accent" disabled={pending || disabled}>
            <Download className="icon-sm" aria-hidden="true" />Download PNG
          </Action>
        </footer>
      </form>
    </Popover>
  </>;
}

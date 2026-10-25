import { Copy, Download, LoaderCircle, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Action } from "../../ui/Controls";
import type { ImageMode } from "../../types/soundings";
import type { SoundingView } from "../../types/soundingView";
import { useObjectUrl } from "./soundingQueries";
import styles from "./Viewer.module.css";

export function SoundingExport({ view, quality, ready, busy, filename }: {
  view: SoundingView | undefined; quality: ImageMode; ready: boolean; busy: boolean; filename: string;
}) {
  const url = useObjectUrl(view?.image);
  const [notice, setNotice] = useState<{ image: Blob; text: string; pending: boolean } | null>(null);
  const exporting = notice?.pending === true;
  const enabled = ready && url !== null && view !== undefined && !exporting;
  const StatusIcon = busy || exporting ? LoaderCircle : ready ? null : TriangleAlert;
  const exportImage = async (copy: boolean): Promise<void> => {
    if (!enabled) return;
    setNotice({ image: view.image, text: copy ? "Copying PNG…" : "Saving PNG…", pending: true });
    try {
      if (copy) {
        if (!("ClipboardItem" in window) || !("clipboard" in navigator)) throw new Error("unavailable");
        await navigator.clipboard.write([new ClipboardItem({ "image/png": view.image })]);
      } else {
        const link = document.createElement("a");
        link.href = url; link.download = filename; link.click();
      }
      setNotice({ image: view.image, text: copy ? "Full-resolution image copied." : "Full-resolution PNG exported.", pending: false });
    } catch {
      setNotice({ image: view.image, text: copy ? "Clipboard access was unavailable. Use Export PNG to save the image." : "The PNG could not be saved. Try Export PNG again.", pending: false });
    }
  };
  return <>
    <div className={styles.exportStatus} role="status" aria-label="Sounding export status" aria-busy={busy || exporting} data-ready={ready}>
      <div className={styles.exportHeading}><span className={styles.groupLabel}>Export</span>
        <span className={styles.exportState}>{StatusIcon === null ? null : <StatusIcon className="icon-sm" aria-hidden="true" />}{exporting ? notice.text : ready ? "Ready to export" : busy ? `Preparing ${quality.toUpperCase()} PNG…` : "PNG unavailable"}</span>
      </div>
      <div className={styles.exportDetails}>
        <div><strong>{quality.toUpperCase()} PNG</strong><span>Lossless{ready && view !== undefined ? ` · ${String(view.width)} × ${String(view.height)}` : ""}</span></div>
        <span className={styles.exportSize} aria-label="Estimated PNG size" title={ready && view !== undefined ? `${view.image.size.toLocaleString()} bytes` : undefined}>{ready && view !== undefined
          ? `~${(view.image.size / 1024 ** 2).toFixed(2)} MiB` : "Size pending"}</span>
      </div>
    </div>
    <div className={styles.exportButtons}>
      <Action tone="accent" disabled={!enabled} onClick={() => { void exportImage(false); }}><Download className="icon-sm" aria-hidden="true" />Export PNG</Action>
      <Action aria-label="Copy sounding image" disabled={!enabled} onClick={() => { void exportImage(true); }}><Copy className="icon-sm" aria-hidden="true" />Copy</Action>
    </div>
    {ready && notice?.image === view?.image ? <p className={styles.exportNotice} role="status">{notice?.text}</p> : null}
  </>;
}

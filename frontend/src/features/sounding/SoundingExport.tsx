import { Copy, Download, LoaderCircle, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { useUiStore } from "../../stores/uiStore";
import { Action } from "../../ui/Controls";
import type { ImageMode } from "../../types/soundings";
import type { SoundingView } from "../../types/soundingView";
import { useObjectUrl } from "./soundingQueries";
import { annotatedSoundingPng, editedLevelAnnotations } from "./exportAnnotations";
import { SourceSelect } from "./SourceFields";
import styles from "./Viewer.module.css";

export function SoundingExport({ view, quality, onQualityChange, ready, busy, filename }: {
  view: SoundingView | undefined; quality: ImageMode; ready: boolean; busy: boolean; filename: string;
  onQualityChange: (quality: ImageMode) => void;
}) {
  const units = useUiStore((state) => state.units);
  const url = useObjectUrl(view?.image);
  const annotations = view === undefined ? [] : editedLevelAnnotations(view, units);
  const [notice, setNotice] = useState<{ image: Blob; text: string; pending: boolean; failed: boolean } | null>(null);
  const exporting = notice?.pending === true;
  const enabled = ready && url !== null && view !== undefined && !exporting;
  const currentNotice = ready && notice?.image === view?.image ? notice : null;
  const StatusIcon = busy || exporting ? LoaderCircle : ready && !currentNotice?.failed ? null : TriangleAlert;
  const description = annotations.length === 0 ? "Lossless PNG" : "Lossless PNG with edited levels in your display units";
  const exportImage = async (copy: boolean): Promise<void> => {
    if (!enabled) return;
    setNotice({ image: view.image, text: copy ? "Copying…" : "Exporting…", pending: true, failed: false });
    try {
      if (copy) {
        if (!("ClipboardItem" in window) || !("clipboard" in navigator)) throw new Error("unavailable");
        await navigator.clipboard.write([new ClipboardItem({ "image/png": annotations.length === 0 ? view.image : annotatedSoundingPng(view, annotations) })]);
      } else {
        const annotatedUrl = annotations.length === 0 ? null : URL.createObjectURL(await annotatedSoundingPng(view, annotations));
        const link = document.createElement("a");
        try { link.href = annotatedUrl ?? url; link.download = filename; link.click(); }
        finally { if (annotatedUrl !== null) window.setTimeout(() => { URL.revokeObjectURL(annotatedUrl); }, 1000); }
      }
      setNotice({ image: view.image, text: copy ? "Copied" : "Exported", pending: false, failed: false });
    } catch {
      setNotice({ image: view.image, text: copy ? "Copy unavailable. Use Export PNG." : "Export failed. Try again.", pending: false, failed: true });
    }
  };
  return <>
    <div className={styles.exportStatus} role="status" aria-label="Sounding export status" aria-busy={busy || exporting} data-ready={ready} data-error={currentNotice?.failed === true}>
      <div className={styles.exportHeading}><span className={styles.groupLabel}>Export</span>
        <span className={styles.exportState}>{StatusIcon === null ? null : <StatusIcon className="icon-sm" data-loading={busy || exporting} aria-hidden="true" />}{exporting ? notice.text : currentNotice?.failed ? "Try again" : currentNotice?.text ?? (ready ? "Ready" : busy ? "Preparing…" : "Unavailable")}</span>
      </div>
      <div className={styles.exportDetails}>
        <span title={description}>{ready && view !== undefined ? `${String(view.width)} × ${String(view.height)}` : "Dimensions pending"}</span>
        <span className={styles.exportSize} aria-label="Estimated PNG size" title={ready && view !== undefined ? `${view.image.size.toLocaleString()} bytes` : undefined}>{ready && view !== undefined
          ? `~${(view.image.size / 1024 ** 2).toFixed(2)} MiB` : "—"}</span>
      </div>
    </div>
    <SourceSelect<ImageMode> label="Image resolution" value={quality} disabled={busy || exporting} compact options={[{ value: "uhd", label: "UHD · lossless" }, { value: "hd", label: "HD · lossless" }]} onChange={onQualityChange} />
    <div className={styles.exportButtons}>
      <Action tone="accent" disabled={!enabled} title={description} aria-description={description} onClick={() => { void exportImage(false); }}><Download className="icon-sm" aria-hidden="true" />Export PNG</Action>
      <Action aria-label="Copy sounding image" disabled={!enabled} title={description} aria-description={description} onClick={() => { void exportImage(true); }}><Copy className="icon-sm" aria-hidden="true" />Copy</Action>
    </div>
    {currentNotice?.failed ? <p className={styles.exportNotice} role="alert">{currentNotice.text}</p> : null}
  </>;
}

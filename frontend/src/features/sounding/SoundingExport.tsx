import { Copy, Download, LoaderCircle, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { copyPngToClipboard, useCanCopyPng } from "../../lib/imageClipboard";
import { useUiStore } from "../../stores/uiStore";
import { Action } from "../../ui/Controls";
import type { ImageMode } from "../../types/soundings";
import type { SoundingView } from "../../types/soundingView";
import { useObjectUrl } from "./soundingQueries";
import { annotatedSoundingPng, editedLevelAnnotations } from "./exportAnnotations";
import { SourceSelect } from "./SourceFields";
import styles from "./Viewer.module.css";
import { Notice } from "../../components/Notice";

export function SoundingExport({ view, quality, onQualityChange, ready, busy, filename }: {
  view: SoundingView | undefined; quality: ImageMode; ready: boolean; busy: boolean; filename: string;
  onQualityChange: (quality: ImageMode) => void;
}) {
  const units = useUiStore((state) => state.units);
  const url = useObjectUrl(view?.image);
  const annotations = view === undefined ? [] : editedLevelAnnotations(view, units);
  const [notice, setNotice] = useState<{ image: Blob; text: string; pending: boolean; failed: boolean; copy?: boolean } | null>(null);
  const currentNotice = ready && notice?.image === view?.image ? notice : null;
  const exporting = currentNotice?.pending === true;
  const enabled = ready && url !== null && view !== undefined && !exporting;
  const canCopy = useCanCopyPng();
  const StatusIcon = busy || exporting ? LoaderCircle : ready && !currentNotice?.failed ? null : TriangleAlert;
  const description = annotations.length === 0 ? "Lossless PNG" : "Lossless PNG with edited levels in your display units";
  const exportImage = async (copy: boolean): Promise<void> => {
    if (!enabled) return;
    const operation = { image: view.image, text: copy ? "Copying…" : "Exporting…", pending: true, failed: false };
    setNotice(operation);
    try {
      if (copy) {
        await copyPngToClipboard(annotations.length === 0 ? view.image : () => annotatedSoundingPng(view, annotations));
      } else {
        const annotatedUrl = annotations.length === 0 ? null : URL.createObjectURL(await annotatedSoundingPng(view, annotations));
        const link = document.createElement("a");
        try { link.href = annotatedUrl ?? url; link.download = filename; link.click(); }
        finally { if (annotatedUrl !== null) window.setTimeout(() => { URL.revokeObjectURL(annotatedUrl); }, 1000); }
      }
      setNotice((current) => current === operation ? { image: view.image, text: copy ? "Copied" : "Exported", pending: false, failed: false } : current);
    } catch {
      setNotice((current) => current === operation ? { image: view.image, text: copy ? "Clipboard access is unavailable. Save the image as a PNG." : "The image could not be saved. Try exporting again.", pending: false, failed: true, copy } : current);
    }
  };
  return <>
    <div className={styles.exportStatus} role="status" aria-label="Sounding export status" aria-busy={busy || exporting} data-ready={ready} data-error={currentNotice?.failed === true}>
      <div className={styles.exportHeading}><span className={styles.groupLabel}>Export</span>
        <span className={styles.exportState}>{StatusIcon === null ? null : <StatusIcon className="icon-sm" data-loading={busy || exporting} aria-hidden="true" />}{exporting ? currentNotice.text : currentNotice?.failed ? "Try again" : currentNotice?.text ?? (ready ? "Ready" : busy ? "Preparing…" : "Unavailable")}</span>
      </div>
      <div className={styles.exportDetails}>
        <span title={description}>{ready && view !== undefined ? `${String(view.width)} × ${String(view.height)}` : "Dimensions pending"}</span>
        <span className={styles.exportSize} aria-label="Estimated PNG size" title={ready && view !== undefined ? `${view.image.size.toLocaleString()} bytes` : undefined}>{ready && view !== undefined
          ? `~${(view.image.size / 1024 ** 2).toFixed(2)} MiB` : "—"}</span>
      </div>
    </div>
    <SourceSelect<ImageMode> label="Image resolution" value={quality} disabled={busy || exporting} options={[{ value: "uhd", label: "UHD · lossless" }, { value: "hd", label: "HD · lossless" }]} onChange={onQualityChange} />
    <div className={styles.exportButtons}>
      <Action tone="accent" disabled={!enabled} title={description} aria-description={description} onClick={() => { void exportImage(false); }}><Download className="icon-sm" aria-hidden="true" />Export PNG</Action>
      {canCopy ? <Action aria-label="Copy sounding image" disabled={!enabled} title={description} aria-description={description} onClick={() => { void exportImage(true); }}><Copy className="icon-sm" aria-hidden="true" />Copy</Action> : null}
    </div>
    {currentNotice?.failed ? <Notice tone="danger" role="alert" title={currentNotice.copy ? "Image copy unavailable" : "Image export failed"}
      icon={currentNotice.copy ? Copy : Download} message={currentNotice.text}
      actions={<Action tone="accent" disabled={!enabled} onClick={() => { void exportImage(false); }}>Export PNG again</Action>} /> : null}
  </>;
}
